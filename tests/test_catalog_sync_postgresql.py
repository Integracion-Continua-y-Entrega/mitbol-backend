"""Criterios de HU-008 en PostgreSQL aislado; todos los datos son simulados."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, Product, ProductComplement, ProductModifier, ProductModifierAssignment, ProductVariant,
)
from app.services.catalog_errors import (
    CatalogPersistenceError, CatalogReferenceError, CatalogSyncBusyError,
    CatalogTargetError, CatalogValidationError,
)
from app.services.loyverse_client import LoyverseAPIClient, LoyverseTransientError
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY, SyncService

DELETED_AT = "2026-11-01T01:00:00Z"


def rows(db, model):
    table = model.__table__
    return [dict(row) for row in db.connection.execute(
        select(table).order_by(*table.primary_key.columns),
    ).mappings()]


def all_rows(db):
    return {name: [dict(row) for row in db.connection.execute(
        select(table).order_by(*table.primary_key.columns),
    ).mappings()] for name, table in Base.metadata.tables.items()}


def by_external_id(db, model):
    return {row["loyverse_id"]: row for row in rows(db, model)}


def test_insertion_relations_and_repeated_upserts(sync_db, commercial_snapshot):
    first = sync_db.service.persist_snapshot(commercial_snapshot)
    baseline = all_rows(sync_db)
    second = sync_db.service.persist_snapshot(commercial_snapshot)
    assert all_rows(sync_db) == baseline
    assert [len(rows(sync_db, model)) for model in (
        Product, ProductVariant, ProductModifier, ProductComplement, ProductModifierAssignment,
    )] == [3, 5, 2, 5, 4]
    assert first["assignments_inserted"] == 4
    assert second["assignments_inserted"] == 0
    products = by_external_id(sync_db, Product)
    variants = by_external_id(sync_db, ProductVariant)
    modifiers = by_external_id(sync_db, ProductModifier)
    options = by_external_id(sync_db, ProductComplement)
    assert variants["single"]["product_id"] == products["burger"]["product_id"]
    assert variants["regular"]["product_id"] == products["fries"]["product_id"]
    assert options["cheese"]["product_modifier_id"] == modifiers["extras"]["product_modifier_id"]
    assert options["mint"]["product_modifier_id"] == modifiers["herbs"]["product_modifier_id"]
    assert options["no-onion"]["price"] == Decimal("0")
    actual_links = {(row["product_variant_id"], row["product_modifier_id"])
                    for row in rows(sync_db, ProductModifierAssignment)}
    assert actual_links == {
        (variants[name]["product_variant_id"], modifiers[group]["product_modifier_id"])
        for name, group in [("single", "extras"), ("double", "extras"), ("small", "herbs"), ("large", "herbs")]
    }


def test_commercial_updates_preserve_local_fields_and_primary_keys(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    before = all_rows(sync_db)
    sync_db.connection.execute(update(Product).where(Product.loyverse_id == "burger").values(
        description="Descripción MITBOL", is_available=False, is_featured=True, is_new=False,
    ))
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["item_name"] = "Hamburguesa actualizada"
    changed.items[0]["description"] = "No sobrescribir la descripción local"
    changed.items[0]["variants"][0]["stores"][0]["price"] = Decimal("90.1234567890123456789")
    changed.modifiers[0]["name"] = "Extras nuevos"
    changed.modifiers[0]["modifier_options"][0]["price"] = Decimal("16.25")
    sync_db.service.persist_snapshot(changed)
    product = by_external_id(sync_db, Product)["burger"]
    assert product["name"] == "Hamburguesa actualizada"
    assert (product["description"], product["is_available"], product["is_featured"], product["is_new"]) == (
        "Descripción MITBOL", False, True, False,
    )
    assert by_external_id(sync_db, ProductVariant)["single"]["price"] == Decimal("90.1234567890123456789")
    assert by_external_id(sync_db, ProductComplement)["cheese"]["price"] == Decimal("16.25")
    for name in ("Products", "ProductVariants", "ProductModifiers", "ProductComplements"):
        table = Base.metadata.tables[name]
        primary_key = next(iter(table.primary_key.columns)).name
        assert {(row["loyverse_id"], row[primary_key]) for row in before[name]} == {
            (row["loyverse_id"], row[primary_key]) for row in all_rows(sync_db)[name]
        }


def test_fresh_snapshot_changes_only_sync_timestamp(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    sync_db.connection.execute(update(Product).where(Product.loyverse_id == "burger").values(
        description="Enriquecimiento local", is_available=False, is_featured=True, is_new=False,
    ))
    sync_db.connection.execute(update(ProductVariant).where(ProductVariant.loyverse_id == "double").values(
        is_available=False,
    ))
    before = all_rows(sync_db)
    fresh = replace(commercial_snapshot, fetched_at=commercial_snapshot.fetched_at + timedelta(minutes=1))
    result = sync_db.service.persist_snapshot(fresh)
    after = all_rows(sync_db)

    def without_timestamp(records):
        return [{key: value for key, value in row.items() if key != "synced_at"} for row in records]

    for table_name, records in after.items():
        assert without_timestamp(records) == without_timestamp(before[table_name])
        if table_name != "ProductModifierAssignments":
            assert all(row["synced_at"] == fresh.fetched_at for row in records)
    assert result["assignments_inserted"] == result["assignments_removed"] == 0


def test_only_double_price_changes_from_119_to_129(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    sync_db.connection.execute(update(Product).where(Product.loyverse_id == "burger").values(
        description="Descripción local conservada", is_available=False, is_featured=True, is_new=False,
    ))
    sync_db.connection.execute(update(ProductVariant).where(ProductVariant.loyverse_id == "double").values(
        is_available=False,
    ))
    before = all_rows(sync_db)
    old_double = by_external_id(sync_db, ProductVariant)["double"]
    assert old_double["price"] == Decimal("119.00")
    changed = deepcopy(commercial_snapshot)
    changed = replace(changed, fetched_at=changed.fetched_at + timedelta(minutes=2))
    changed.items[0]["variants"][1]["stores"][0]["price"] = Decimal("129.00")
    # El precio de tienda debe ganar incluso si el default sigue en 119.
    assert changed.items[0]["variants"][1]["default_price"] == 119
    result = sync_db.service.persist_snapshot(changed)
    after = all_rows(sync_db)
    actual_double = by_external_id(sync_db, ProductVariant)["double"]
    assert actual_double["price"] == Decimal("129.00")
    assert actual_double["product_variant_id"] == old_double["product_variant_id"]
    assert actual_double["is_available"] is False
    expected = deepcopy(before)
    next(row for row in expected["ProductVariants"] if row["loyverse_id"] == "double")["price"] = Decimal("129.00")

    def without_timestamp(rows):
        return [{key: value for key, value in row.items() if key != "synced_at"} for row in rows]

    for table, records in after.items():
        assert without_timestamp(records) == without_timestamp(expected[table])
        if table != "ProductModifierAssignments":
            assert all(row["synced_at"] == changed.fetched_at for row in records)
    assert result["assignments_inserted"] == result["assignments_removed"] == 0


def test_only_obsolete_commercial_assignments_are_removed(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["modifier_ids"] = []
    result = sync_db.service.persist_snapshot(changed)
    assert result["assignments_removed"] == 2
    assert len(rows(sync_db, ProductModifierAssignment)) == 2
    assert len(rows(sync_db, ProductVariant)) == 5
    assert len(rows(sync_db, ProductComplement)) == 5


@pytest.mark.parametrize("value", [None, -1, Decimal("NaN"), Decimal("Infinity"), True, "10.00", 0.1])
def test_invalid_price_reverts_without_overwriting_previous_data(sync_db, commercial_snapshot, value):
    sync_db.service.persist_snapshot(commercial_snapshot)
    before = all_rows(sync_db)
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["item_name"] = "Cambio que no debe guardarse"
    changed.items[0]["variants"][0]["stores"][0]["price"] = value
    with pytest.raises(CatalogValidationError):
        sync_db.service.persist_snapshot(changed)
    assert all_rows(sync_db) == before


def test_product_tombstone_preserves_rows_prices_and_local_availability(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    old_variants = by_external_id(sync_db, ProductVariant)
    deleted = {"id": "burger", "deleted_at": DELETED_AT}
    changed = replace(commercial_snapshot, items=(deleted, *commercial_snapshot.items[1:]))
    sync_db.service.persist_snapshot(changed)
    product = by_external_id(sync_db, Product)["burger"]
    variants = by_external_id(sync_db, ProductVariant)
    assert product["is_active"] is False and product["is_available"] is True
    for identifier in ("single", "double"):
        assert variants[identifier]["is_available"] is True
        assert variants[identifier]["is_commercially_available"] is False
        assert variants[identifier]["price"] == old_variants[identifier]["price"]
        assert variants[identifier]["product_variant_id"] == old_variants[identifier]["product_variant_id"]
    assert len(rows(sync_db, Product)) == 3
    assert len(rows(sync_db, ProductModifierAssignment)) == 2
    sync_db.service.persist_snapshot(commercial_snapshot)
    assert by_external_id(sync_db, Product)["burger"]["is_active"] is True
    assert len(rows(sync_db, ProductModifierAssignment)) == 4


@pytest.mark.parametrize("all_variants", [False, True])
def test_variant_tombstones_do_not_need_prices(sync_db, commercial_snapshot, all_variants):
    sync_db.service.persist_snapshot(commercial_snapshot)
    changed = deepcopy(commercial_snapshot)
    variants = changed.items[0]["variants"]
    for position in range(len(variants) if all_variants else 1):
        variants[position] = {"variant_id": variants[position]["variant_id"], "item_id": "burger", "deleted_at": DELETED_AT}
    sync_db.service.persist_snapshot(changed)
    actual = by_external_id(sync_db, ProductVariant)
    assert actual["single"]["is_available"] is True
    assert actual["double"]["is_available"] is True
    assert actual["single"]["is_commercially_available"] is False
    assert actual["double"]["is_commercially_available"] is (not all_variants)
    assert len(actual) == 5


def test_deleted_group_keeps_identity_and_disables_its_options(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    changed = replace(commercial_snapshot, modifiers=(
        {"id": "extras", "deleted_at": DELETED_AT}, commercial_snapshot.modifiers[1],
    ))
    sync_db.service.persist_snapshot(changed)
    assert len(rows(sync_db, ProductModifier)) == 2
    options = by_external_id(sync_db, ProductComplement)
    assert all(options[key]["is_active"] is False for key in ("cheese", "bacon", "no-onion"))
    assert options["mint"]["is_active"] is True
    assert len(rows(sync_db, ProductModifierAssignment)) == 2


def test_option_tombstone_preserves_its_price(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    old = by_external_id(sync_db, ProductComplement)["cheese"]
    changed = deepcopy(commercial_snapshot)
    changed.modifiers[0]["modifier_options"][0] = {"id": "cheese", "deleted_at": DELETED_AT}
    sync_db.service.persist_snapshot(changed)
    actual = by_external_id(sync_db, ProductComplement)["cheese"]
    assert actual["is_active"] is False
    assert actual["price"] == old["price"]
    assert actual["product_complement_id"] == old["product_complement_id"]


def test_unknown_deleted_entities_do_not_create_fake_rows(sync_db, commercial_snapshot):
    deleted = {"id": "unknown", "deleted_at": DELETED_AT}
    snapshot = replace(commercial_snapshot, items=(deleted,), modifiers=(deleted,))
    sync_db.service.persist_snapshot(snapshot)
    assert all(not records for records in all_rows(sync_db).values())


def test_absence_alone_does_not_retire_previously_valid_products(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    snapshot = replace(commercial_snapshot, items=commercial_snapshot.items[1:])
    sync_db.service.persist_snapshot(snapshot)
    assert by_external_id(sync_db, Product)["burger"]["is_active"] is True
    assert by_external_id(sync_db, ProductVariant)["single"]["is_available"] is True


@pytest.mark.parametrize("entity", ["variant", "option"])
def test_external_id_cannot_be_reassigned_to_another_parent(sync_db, commercial_snapshot, entity):
    sync_db.service.persist_snapshot(commercial_snapshot)
    before = all_rows(sync_db)
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["item_name"] = "Debe revertirse"
    if entity == "variant":
        variant = changed.items[0]["variants"].pop(0)
        variant["item_id"] = "drink"
        changed.items[1]["variants"].append(variant)
    else:
        option = changed.modifiers[0]["modifier_options"].pop(0)
        changed.modifiers[1]["modifier_options"].append(option)
    with pytest.raises(CatalogReferenceError):
        sync_db.service.persist_snapshot(changed)
    assert all_rows(sync_db) == before


@pytest.mark.parametrize("previous_catalog", [False, True])
def test_real_postgresql_error_rolls_back_all_previous_upserts(sync_db, commercial_snapshot, monkeypatch, previous_catalog):
    if previous_catalog:
        sync_db.service.persist_snapshot(commercial_snapshot)
    before = all_rows(sync_db)
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["item_name"] = "Actualización que debe revertirse"
    changed.items[0]["variants"][0]["stores"][0]["price"] = 999
    original = sync_db.service._upsert

    def fail_on_complement(session, model, *args, **kwargs):
        if model is ProductComplement:
            # Error real del servidor después de insertar productos y variantes.
            session.execute(text("SELECT 1 / 0"))
        return original(session, model, *args, **kwargs)

    monkeypatch.setattr(sync_db.service, "_upsert", fail_on_complement)
    with pytest.raises(CatalogPersistenceError):
        sync_db.service.persist_snapshot(changed)
    assert all_rows(sync_db) == before
    if not previous_catalog:
        with sync_db.engine.begin() as other:
            assert other.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))) is True


@pytest.mark.parametrize("value", [None, -5])
def test_invalid_complement_price_preserves_previous_catalog(sync_db, commercial_snapshot, value):
    sync_db.service.persist_snapshot(commercial_snapshot)
    before = all_rows(sync_db)
    changed = deepcopy(commercial_snapshot)
    changed.modifiers[0]["modifier_options"][0]["price"] = value
    with pytest.raises(CatalogValidationError):
        sync_db.service.persist_snapshot(changed)
    assert all_rows(sync_db) == before


def test_updates_preserve_product_and_variant_local_availability(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    sync_db.connection.execute(update(Product).where(Product.loyverse_id == "burger").values(is_available=False))
    sync_db.connection.execute(update(ProductVariant).where(ProductVariant.loyverse_id == "single").values(
        is_available=False,
    ))
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["variants"][0]["stores"][0]["available_for_sale"] = True
    changed.items[0]["variants"][1]["stores"][0]["available_for_sale"] = False
    sync_db.service.persist_snapshot(changed)
    assert by_external_id(sync_db, Product)["burger"]["is_available"] is False
    assert by_external_id(sync_db, ProductVariant)["single"]["is_available"] is False
    assert by_external_id(sync_db, ProductVariant)["double"]["is_available"] is True
    assert by_external_id(sync_db, ProductVariant)["single"]["is_commercially_available"] is True
    assert by_external_id(sync_db, ProductVariant)["double"]["is_commercially_available"] is False


def test_new_variant_uses_actual_initial_store_availability(sync_db, commercial_snapshot):
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["variants"][0]["stores"][0]["available_for_sale"] = False
    sync_db.service.persist_snapshot(changed)
    assert by_external_id(sync_db, ProductVariant)["single"]["is_available"] is True
    assert by_external_id(sync_db, ProductVariant)["double"]["is_available"] is True
    assert by_external_id(sync_db, ProductVariant)["single"]["is_commercially_available"] is False
    assert by_external_id(sync_db, ProductVariant)["double"]["is_commercially_available"] is True


@pytest.mark.parametrize("local", [False, True])
def test_commercial_changes_in_both_directions_never_change_local_preference(sync_db, commercial_snapshot, local):
    sync_db.service.persist_snapshot(commercial_snapshot)
    sync_db.connection.execute(update(ProductVariant).where(ProductVariant.loyverse_id == "single").values(is_available=local))
    before = by_external_id(sync_db, ProductVariant)["single"]
    for commercial in (False, True, False, True):
        snapshot = deepcopy(commercial_snapshot)
        snapshot.items[0]["variants"][0]["stores"][0]["available_for_sale"] = commercial
        sync_db.service.persist_snapshot(snapshot)
        actual = by_external_id(sync_db, ProductVariant)["single"]
        assert actual["is_available"] is local
        assert actual["is_commercially_available"] is commercial
        assert {key: value for key, value in actual.items() if key != "is_commercially_available"} == {
            key: value for key, value in before.items() if key != "is_commercially_available"
        }


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("retire_product", [False, True])
def test_retirement_and_reappearance_keep_local_preference_and_enrichment(sync_db, commercial_snapshot, local, retire_product):
    from app.models import ProductImage, ProductTag, Tag
    sync_db.service.persist_snapshot(commercial_snapshot)
    before = by_external_id(sync_db, ProductVariant)["single"]
    sync_db.connection.execute(update(ProductVariant).where(ProductVariant.loyverse_id == "single").values(is_available=local))
    sync_db.connection.execute(update(Product).where(Product.loyverse_id == "burger").values(description="Local"))
    with sync_db.sessions() as db:
        db.add(ProductImage(product_variant_id=before["product_variant_id"], image_url="/media/keep.webp", display_order=0))
        db.add(ProductTag(product_variant_id=before["product_variant_id"], tag=Tag(name="picante")))
        db.commit()
    enrichment_before = {model.__tablename__: rows(sync_db, model) for model in (ProductImage, ProductTag, Tag)}
    snapshot = deepcopy(commercial_snapshot)
    if retire_product:
        snapshot.items[0].clear()
        snapshot.items[0].update(id="burger", deleted_at=DELETED_AT)
    else:
        snapshot.items[0]["variants"][0].clear()
        snapshot.items[0]["variants"][0].update(variant_id="single", item_id="burger", deleted_at=DELETED_AT)
    sync_db.service.persist_snapshot(snapshot)
    retired = by_external_id(sync_db, ProductVariant)["single"]
    assert retired["is_available"] is local and retired["is_commercially_available"] is False
    sync_db.service.persist_snapshot(commercial_snapshot)
    restored = by_external_id(sync_db, ProductVariant)["single"]
    assert restored["is_available"] is local and restored["is_commercially_available"] is True
    assert restored["product_variant_id"] == before["product_variant_id"]
    assert restored["price"] == before["price"]
    assert by_external_id(sync_db, Product)["burger"]["description"] == "Local"
    assert {model.__tablename__: rows(sync_db, model) for model in (ProductImage, ProductTag, Tag)} == enrichment_before


@pytest.mark.parametrize("mode", ["missing", "null", "string", "missing-store"])
def test_insufficient_commercial_data_is_rejected_without_inventing_availability(sync_db, commercial_snapshot, mode):
    sync_db.service.persist_snapshot(commercial_snapshot)
    before = all_rows(sync_db)
    snapshot = deepcopy(commercial_snapshot)
    store = snapshot.items[0]["variants"][0]["stores"][0]
    if mode == "missing":
        del store["available_for_sale"]
    elif mode == "null":
        store["available_for_sale"] = None
    elif mode == "string":
        store["available_for_sale"] = "true"
    else:
        store["store_id"] = "another-store"
    with pytest.raises(CatalogValidationError):
        sync_db.service.persist_snapshot(snapshot)
    assert all_rows(sync_db) == before


def test_another_transaction_cannot_sync_while_lock_is_held(sync_db, commercial_snapshot):
    with sync_db.engine.begin() as other:
        assert other.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))) is True
        with pytest.raises(CatalogSyncBusyError):
            sync_db.service.persist_snapshot(commercial_snapshot)
        assert all(not records for records in all_rows(sync_db).values())
    sync_db.service.persist_snapshot(commercial_snapshot)
    assert len(rows(sync_db, Product)) == 3


def test_commit_releases_the_transaction_lock(sync_db, commercial_snapshot):
    service = SyncService(sessionmaker(bind=sync_db.engine),
                          expected_database=sync_db.engine.url.database, store_id="test-store")
    empty = replace(commercial_snapshot, items=(), modifiers=())
    service.persist_snapshot(empty)
    with sync_db.engine.begin() as other:
        assert other.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))) is True


def test_wrong_database_or_store_is_rejected_before_writing(sync_db, commercial_snapshot):
    wrong_database = SyncService(sync_db.sessions, expected_database="mitbol_dev", store_id="test-store")
    with pytest.raises(CatalogTargetError):
        wrong_database.persist_snapshot(commercial_snapshot)
    with pytest.raises(CatalogValidationError):
        sync_db.service.persist_snapshot(replace(commercial_snapshot, store={"id": "another-store"}))
    with pytest.raises(CatalogTargetError):
        SyncService(sync_db.sessions, expected_database="mitbol_db", store_id="test-store")
    assert all(not records for records in all_rows(sync_db).values())


@pytest.mark.asyncio
async def test_mocked_download_and_persistence_hold_lock_from_the_first_get(sync_db, commercial_snapshot):
    calls = []

    def respond(request):
        assert request.method == "GET"
        with sync_db.engine.begin() as other:
            assert other.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))) is False
        endpoint = request.url.path.removeprefix("/v1.0")
        calls.append(endpoint)
        if endpoint == "/stores":
            return httpx.Response(200, json={"stores": [commercial_snapshot.store]})
        assert request.url.params["show_deleted"] == "true"
        if endpoint == "/items":
            return httpx.Response(200, json={"items": list(commercial_snapshot.items)})
        assert endpoint == "/modifiers"
        return httpx.Response(200, json={"modifiers": list(commercial_snapshot.modifiers)})

    async with LoyverseAPIClient("fake-token", transport=httpx.MockTransport(respond)) as client:
        result = await sync_db.service.synchronize(client)
    assert calls == ["/stores", "/items", "/modifiers"]
    assert result["products_processed"] == 3
    assert len(rows(sync_db, ProductVariant)) == 5


@pytest.mark.asyncio
async def test_http_failure_preserves_previous_catalog(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    before = all_rows(sync_db)
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    async with LoyverseAPIClient("fake-token", transport=transport, sleep=AsyncMock()) as client:
        with pytest.raises(LoyverseTransientError):
            await sync_db.service.synchronize(client)
    assert all_rows(sync_db) == before

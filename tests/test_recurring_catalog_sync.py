"""HU-008 recurrente: PostgreSQL desechable y HTTP simulado; nunca servicios reales."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, event, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.models import (
    Product, ProductComplement, ProductImage, ProductModifier, ProductTag,
    ProductVariant, Tag, catalog_metadata,
)
from app.cli import sync_catalog_recurrent as cli
from app.services.catalog_errors import (
    CatalogPersistenceError, CatalogReferenceError, CatalogSyncBusyError,
    CatalogTargetError, CatalogValidationError,
)
from app.services.loyverse_client import (
    LoyverseAPIClient, LoyverseAPIError, LoyverseConfigurationError,
    LoyverseResponseError, LoyverseTransientError,
)
from app.services.recurring_catalog_sync import RecurringCatalogSync
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY, SyncService


class FakeCatalogAPI:
    """Ejercita el cliente HTTP real y todas sus páginas con transporte en memoria."""

    def __init__(self, snapshot, fault=None):
        self.snapshot, self.fault, self.requests = snapshot, fault, []

    def handle(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        key = request.url.path.rsplit("/", 1)[-1]
        if self.fault:
            response = self.fault(request, key)
            if response is not None:
                return response
        if key == "stores":
            return httpx.Response(200, json={key: [self.snapshot.store]})
        assert key in {"items", "modifiers"}
        assert request.url.params["show_deleted"] == "true"
        records = getattr(self.snapshot, key)
        start = int(request.url.params.get("cursor", "0"))
        end = start + int(request.url.params["limit"])
        payload = {key: records[start:end]}
        if end < len(records):
            payload["cursor"] = str(end)
        return httpx.Response(200, json=payload)

    def client(self, **kwargs):
        return LoyverseAPIClient(access_token="synthetic-not-a-credential", page_size=1,
            max_attempts=1, transport=httpx.MockTransport(self.handle), **kwargs)


@pytest.fixture
def recurring_db(sync_test_database):
    engine = sync_test_database
    assert engine.url.database.startswith("mitbol_test_hu008_")
    assert engine.url.host == "127.0.0.1"

    def clean():
        with engine.begin() as connection:
            for table in reversed(catalog_metadata.sorted_tables):
                connection.execute(table.delete())

    clean()
    sessions = sessionmaker(bind=engine)
    runner = RecurringCatalogSync(sessions, expected_database=engine.url.database,
                                expected_host=engine.url.host, store_id="test-store")
    scope = {"host": engine.url.host, "database": engine.url.database,
             "user": engine.url.username, "store_id": "test-store", "revision": "20261102_0003"}
    try:
        yield SimpleNamespace(engine=engine, sessions=sessions, runner=runner,
            approval={"operation": "catalog_recurrent_v1", "approved": True, **scope})
    finally:
        clean()


def state(db, *, sequences=False):
    with db.engine.connect() as connection:
        result = {name: [dict(row) for row in connection.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()]
            for name, table in catalog_metadata.tables.items()}
        if sequences:
            result["sequences"] = connection.exec_driver_sql(
                "SELECT sequencename, last_value FROM pg_sequences "
                "WHERE schemaname='public' ORDER BY sequencename").all()
        return result


def indexed(db, model):
    return {row["loyverse_id"]: row for row in state(db)[model.__tablename__]}


def run(db, snapshot, *, dry_run=False, api=None, approval=None):
    api = api or FakeCatalogAPI(snapshot)

    async def invoke():
        async with api.client() as client:
            result = await db.runner.run(client, dry_run=dry_run,
                approval=db.approval if approval is None else approval)
            return result, client.page_counts

    report, pages = asyncio.run(invoke())
    return report, pages, api


def test_first_import_and_repeat_preserve_ids_and_report_unchanged(recurring_db, commercial_snapshot):
    db = recurring_db
    first, pages, _ = run(db, commercial_snapshot)
    assert pages == {"/stores": 1, "/items": 3, "/modifiers": 2}
    assert first["status"] == "synchronized" and first["writes"] is True
    assert [first["changes"][key]["new"] for key in
            ("products", "variants", "modifiers", "complements")] == [3, 5, 2, 5]
    before = state(db)
    second, _, _ = run(db, commercial_snapshot)
    assert second["catalog_sha256"] == first["catalog_sha256"]
    assert second["snapshot_at"] > first["snapshot_at"]
    for name, count in (("products", 3), ("variants", 5), ("modifiers", 2), ("complements", 5)):
        assert second["changes"][name] == dict(new=0, updated=0, unchanged=count, retired=0, not_returned=0)
    assert second["changes"]["assignments"] == {"inserted": 0, "removed": 0}
    after = state(db)
    for table, rows in before.items():
        assert [{k: v for k, v in row.items() if k != "synced_at"} for row in rows] == [
            {k: v for k, v in row.items() if k != "synced_at"} for row in after[table]]
    assert all(row["synced_at"] == datetime.fromisoformat(second["snapshot_at"]) for row in after["Products"])


def test_price_names_new_product_variants_groups_and_links(recurring_db, commercial_snapshot):
    db = recurring_db
    run(db, commercial_snapshot)
    changed = deepcopy(commercial_snapshot)
    burger = changed.items[0]
    burger["item_name"] = "Hamburguesa actualizada"
    burger["variants"][0]["option1_value"] = "Sencilla nueva"
    burger["variants"][0]["stores"][0]["price"] = 91.25
    burger["variants"][1]["stores"][0]["available_for_sale"] = False
    triple = deepcopy(burger["variants"][0])
    triple.update(variant_id="triple", option1_value="Triple")
    burger["variants"].append(triple)
    dessert = deepcopy(changed.items[2])
    dessert.update(id="dessert", item_name="Postre")
    dessert["variants"][0].update(variant_id="dessert-small", item_id="dessert")
    changed = replace(changed, items=(*changed.items, dessert))
    changed.modifiers[0].update(name="Extras nuevos", position=3)
    changed.modifiers[0]["modifier_options"][0].update(name="Doble queso", price=17.25, position=2)
    changed.modifiers[0]["modifier_options"].append({"id": "avocado", "name": "Aguacate", "price": 10, "position": 4})
    sauces = {"id": "sauces", "name": "Salsas", "position": 4, "stores": ["test-store"],
              "modifier_options": [{"id": "hot", "name": "Picante", "price": 0, "position": 1}]}
    changed = replace(changed, modifiers=(*changed.modifiers, sauces))
    changed.items[1]["modifier_ids"] = ["extras", "sauces"]
    preview, _, _ = run(db, changed, dry_run=True)
    report, _, _ = run(db, changed)
    assert preview["changes"] == report["changes"]
    assert report["changes"]["products"]["new"] == 1
    assert report["changes"]["products"]["updated"] == 1
    assert report["changes"]["variants"]["new"] == 2
    assert report["changes"]["variants"]["updated"] == 2
    assert report["changes"]["modifiers"]["new"] == 1
    assert report["changes"]["complements"]["new"] == 2
    assert report["changes"]["complements"]["updated"] == 1
    assert report["changes"]["assignments"] == {"inserted": 5, "removed": 2}
    variants, products = indexed(db, ProductVariant), indexed(db, Product)
    assert variants["single"]["price"] == Decimal("91.25")
    assert variants["single"]["name"] == "Sencilla nueva"
    assert variants["double"]["is_commercially_available"] is False
    assert variants["double"]["is_available"] is True
    assert variants["triple"]["product_id"] == products["burger"]["product_id"]
    assert variants["dessert-small"]["product_id"] == products["dessert"]["product_id"]
    assert indexed(db, ProductComplement)["cheese"]["price"] == Decimal("17.25")
    third, _, _ = run(db, changed)
    assert all(third["changes"][key]["new"] == third["changes"][key]["updated"] == 0
               for key in ("products", "variants", "modifiers", "complements"))


def test_local_photos_tags_descriptions_availability_and_flags_survive(recurring_db, commercial_snapshot):
    db = recurring_db
    run(db, commercial_snapshot)
    variant_id = indexed(db, ProductVariant)["single"]["product_variant_id"]
    with db.sessions.begin() as session:
        session.execute(update(Product).where(Product.loyverse_id == "burger").values(
            description="Descripción local", is_available=False, is_featured=True, is_new=False))
        session.execute(update(ProductVariant).where(ProductVariant.loyverse_id == "single").values(is_available=False))
        tag = Tag(name="Local")
        session.add(tag)
        session.flush()
        session.add_all([ProductTag(product_variant_id=variant_id, tag_id=tag.tag_id),
            ProductImage(product_variant_id=variant_id, image_url="/media/products/preserved.webp", display_order=7)])
    before = state(db)
    changed = deepcopy(commercial_snapshot)
    changed.items[0].update(item_name="Nombre comercial nuevo", description="No debe sobrescribir")
    changed.items[0]["variants"][0]["stores"][0]["price"] = 95
    run(db, changed)
    after = state(db)
    for name in ("ProductImages", "Tags", "ProductTags"):
        assert after[name] == before[name]
    product = indexed(db, Product)["burger"]
    assert (product["description"], product["is_available"], product["is_featured"], product["is_new"]) == (
        "Descripción local", False, True, False)
    assert indexed(db, ProductVariant)["single"]["is_available"] is False
    # Bajas explícitas mantienen registros e información local; no se accede a R2.
    tombstone = replace(changed, items=({"id": "burger", "deleted_at": "2026-11-14T00:00:00Z"},))
    preview, _, _ = run(db, tombstone, dry_run=True)
    result, _, _ = run(db, tombstone)
    assert preview["changes"] == result["changes"]
    assert result["changes"]["products"]["retired"] == 1
    assert result["changes"]["variants"]["retired"] == 2
    assert indexed(db, Product)["burger"]["is_active"] is False
    assert indexed(db, ProductVariant)["single"]["is_commercially_available"] is False
    run(db, changed)
    assert indexed(db, Product)["burger"]["is_active"] is True
    assert indexed(db, ProductVariant)["single"]["is_available"] is False
    for name in ("ProductImages", "Tags", "ProductTags"):
        assert state(db)[name] == before[name]


@pytest.mark.parametrize("populated", [False, True])
def test_dry_run_is_read_only_and_does_not_consume_sequences(recurring_db, commercial_snapshot, monkeypatch, populated):
    db = recurring_db
    if populated:
        run(db, commercial_snapshot)
    before = state(db, sequences=True)
    statements = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def forbidden_write(*args):
        pytest.fail("La simulación no debe llamar al escritor")

    monkeypatch.setattr(SyncService, "_write", forbidden_write)
    event.listen(db.engine, "before_cursor_execute", observe)
    try:
        result, _, _ = run(db, commercial_snapshot, dry_run=True)
    finally:
        event.remove(db.engine, "before_cursor_execute", observe)
    assert result["status"] == "dry_run" and result["writes"] is False
    assert result["persistence"] is None
    assert result["approval_template"]["approved"] is False
    assert "SET TRANSACTION READ ONLY" in statements
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE")) for sql in statements)
    assert state(db, sequences=True) == before


@pytest.mark.parametrize("empty", [False, True])
def test_absence_is_preserved_without_inventing_tombstones(recurring_db, commercial_snapshot, empty):
    db = recurring_db
    run(db, commercial_snapshot)
    before = state(db)
    partial = replace(commercial_snapshot, items=() if empty else (commercial_snapshot.items[2],), modifiers=())
    result, _, _ = run(db, partial)
    assert result["changes"]["products"]["not_returned"] == (3 if empty else 2)
    assert result["changes"]["variants"]["not_returned"] == (5 if empty else 4)
    assert result["changes"]["modifiers"]["not_returned"] == 2
    for name in ("ProductModifiers", "ProductComplements", "ProductModifierAssignments"):
        assert state(db)[name] == before[name]
    assert indexed(db, Product)["burger"] == {row["loyverse_id"]: row for row in before["Products"]}["burger"]


@pytest.mark.parametrize("fault,exception", [
    ("page_failure", LoyverseTransientError), ("missing_collection", LoyverseResponseError),
    ("repeated_cursor", LoyverseResponseError), ("invalid_price", CatalogValidationError),
    ("store", LoyverseConfigurationError),
])
def test_incomplete_invalid_or_failed_provider_keeps_previous_rows(recurring_db, commercial_snapshot, fault, exception):
    db = recurring_db
    run(db, commercial_snapshot)
    before = state(db, sequences=True)
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["item_name"] = "No persistir"
    if fault == "invalid_price":
        changed.items[0]["variants"][0]["stores"][0]["price"] = None
    if fault == "store":
        changed = replace(changed, store={"id": "other-store", "name": "Otra tienda"})

    def inject(request, key):
        if key != "items" or not request.url.params.get("cursor"):
            return None
        if fault == "page_failure":
            return httpx.Response(503, json={"secret": "Never logged"})
        if fault == "missing_collection":
            return httpx.Response(200, json={"cursor": "2"})
        if fault == "repeated_cursor":
            return httpx.Response(200, json={"items": [changed.items[1]], "cursor": "1"})
        return None

    with pytest.raises(exception):
        run(db, changed, api=FakeCatalogAPI(changed, inject))
    assert state(db, sequences=True) == before
    # El bloqueo se libera también después de errores HTTP o de validación.
    run(db, commercial_snapshot, dry_run=True)


@pytest.mark.parametrize("dry_run", [False, True])
def test_shared_lock_rejects_concurrent_execution_before_http(recurring_db, commercial_snapshot, dry_run):
    db = recurring_db
    api = FakeCatalogAPI(commercial_snapshot)
    with db.engine.begin() as other:
        assert other.scalar(select(text(f"pg_try_advisory_xact_lock({CATALOG_SYNC_LOCK_KEY})")))
        with pytest.raises(CatalogSyncBusyError):
            run(db, commercial_snapshot, dry_run=dry_run, api=api)
    assert not api.requests
    run(db, commercial_snapshot, dry_run=dry_run)


@pytest.mark.parametrize("dry_run", [False, True])
def test_lock_is_held_while_fetching_every_page(recurring_db, commercial_snapshot, dry_run):
    db = recurring_db

    def check_lock(request, key):
        with db.engine.begin() as other:
            assert not other.scalar(select(text(f"pg_try_advisory_xact_lock({CATALOG_SYNC_LOCK_KEY})")))
        return None

    api = FakeCatalogAPI(commercial_snapshot, check_lock)
    run(db, commercial_snapshot, dry_run=dry_run, api=api)
    assert len(api.requests) == 6


def test_failure_after_dml_rolls_back_all_commercial_changes(recurring_db, commercial_snapshot, monkeypatch):
    db = recurring_db
    run(db, commercial_snapshot)
    before = state(db)
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["item_name"] = "Rollback"
    changed.items[0]["variants"][0]["stores"][0]["price"] = 200
    write = SyncService._write

    def fail_after_writes(service, session, plan):
        write(service, session, plan)
        raise SQLAlchemyError("synthetic failure after DML")

    with monkeypatch.context() as patch:
        patch.setattr(SyncService, "_write", fail_after_writes)
        with pytest.raises(CatalogPersistenceError):
            run(db, changed)
    assert state(db) == before
    run(db, changed)
    assert indexed(db, Product)["burger"]["name"] == "Rollback"


@pytest.mark.parametrize("dry_run", [False, True])
def test_reparented_identity_is_rejected_before_writes(recurring_db, commercial_snapshot, dry_run):
    db = recurring_db
    run(db, commercial_snapshot)
    before = state(db, sequences=True)
    changed = deepcopy(commercial_snapshot)
    moved = changed.items[0]["variants"].pop()
    moved["item_id"] = "drink"
    changed.items[1]["variants"].append(moved)
    with pytest.raises(CatalogReferenceError):
        run(db, changed, dry_run=dry_run)
    assert state(db, sequences=True) == before


@pytest.mark.parametrize("field", ["host", "database", "user", "store_id", "revision", "operation", "approved"])
def test_authorization_is_bound_to_scope_before_any_http(recurring_db, commercial_snapshot, field):
    db = recurring_db
    approval = {**db.approval, field: False if field == "approved" else "other"}
    api = FakeCatalogAPI(commercial_snapshot)
    with pytest.raises(CatalogTargetError):
        run(db, commercial_snapshot, approval=approval, api=api)
    assert not api.requests
    assert not state(db)["Products"]


def test_schema_rejected_before_any_http(recurring_db, commercial_snapshot):
    db = recurring_db
    api = FakeCatalogAPI(commercial_snapshot)
    with db.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE public.alembic_version SET version_num='unknown'")
    try:
        with pytest.raises(CatalogTargetError):
            run(db, commercial_snapshot, api=api)
        assert not api.requests
    finally:
        with db.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE public.alembic_version SET version_num='20261102_0003'")


def test_insufficient_real_postgresql_permissions_rejected_before_http(recurring_db, commercial_snapshot):
    db = recurring_db
    # Rol exclusivo del clúster desechable, sin privilegios de escritura.
    role = "hu008_readonly_" + uuid4().hex[:12]
    with db.engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        connection.exec_driver_sql(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"')
    engine = create_engine(db.engine.url.set(username=role), hide_parameters=True)
    api = FakeCatalogAPI(commercial_snapshot)
    runner = RecurringCatalogSync(sessionmaker(bind=engine), expected_database=engine.url.database,
                                 expected_host=engine.url.host, store_id="test-store")

    async def invoke():
        async with api.client() as client:
            await runner.run(client)

    try:
        with pytest.raises(CatalogTargetError):
            asyncio.run(invoke())
        assert not api.requests
    finally:
        engine.dispose()
        with db.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')


@pytest.fixture
def recurrent_cli(recurring_db, commercial_snapshot, monkeypatch):
    db = recurring_db
    for key, value in {
        "APP_ENV": "test", "ADMIN_ENABLED": "false", "EXPECTED_DATABASE": db.engine.url.database,
        "EXPECTED_DATABASE_HOST": db.engine.url.host, "DATABASE_URL": db.engine.url.render_as_string(hide_password=False),
        "LOYVERSE_ACCESS_TOKEN": "SYNTHETIC_PRIVATE_TOKEN", "LOYVERSE_STORE_ID": "test-store",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MITBOL_RECURRENT_SYNC_APPROVAL", raising=False)
    api = FakeCatalogAPI(commercial_snapshot)
    monkeypatch.setattr(cli, "LoyverseAPIClient", lambda **kwargs: api.client())
    args = ["--expected-database", db.engine.url.database, "--expected-host", db.engine.url.host,
            "--store-id", "test-store"]
    return SimpleNamespace(db=db, api=api, args=args)


def test_cli_default_preview_explicit_execution_and_repeat(recurrent_cli, monkeypatch, capsys):
    context = recurrent_cli
    assert cli.main(context.args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "dry_run" and preview["writes"] is False
    assert not state(context.db)["Products"]
    assert cli.main([*context.args, "--execute"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "validation"
    approval = {**preview["approval_template"], "approved": True}
    monkeypatch.setenv("MITBOL_RECURRENT_SYNC_APPROVAL", json.dumps(approval))
    assert cli.main([*context.args, "--execute"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["changes"]["products"]["new"] == 3
    assert len(state(context.db)["Products"]) == 3
    assert cli.main([*context.args, "--execute"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["changes"]["products"]["unchanged"] == 3
    assert second["changes"]["assignments"] == {"inserted": 0, "removed": 0}


@pytest.mark.parametrize("key,value", [
    ("APP_ENV", ""), ("APP_ENV", "production"), ("ADMIN_ENABLED", "true"),
    ("EXPECTED_DATABASE", "other"), ("EXPECTED_DATABASE_HOST", "other.example.com"),
    ("LOYVERSE_STORE_ID", "other-store"), ("LOYVERSE_ACCESS_TOKEN", ""),
    ("DATABASE_URL", "postgresql://user:SYNTHETIC_PASSWORD@remote.example.com/shared"),
    ("DATABASE_URL", "postgresql://mitbol_render_reader@127.0.0.1/mitbol_dev"),
    ("PGHOSTADDR", "198.51.100.1"),
])
def test_cli_rejects_unauthorized_configuration_without_connecting(recurrent_cli, monkeypatch, capsys, key, value):
    context = recurrent_cli
    monkeypatch.setenv(key, value)

    def forbidden(*args, **kwargs):
        pytest.fail("La configuración inválida debe rechazarse sin abrir conexiones")

    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    assert cli.main(context.args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["writes"] is False
    assert "SYNTHETIC" not in output and "remote.example.com" not in output
    assert not context.api.requests


def test_initial_approval_cannot_authorize_recurrent_execution(recurrent_cli, monkeypatch, capsys):
    context = recurrent_cli
    monkeypatch.setenv("MITBOL_SYNC_APPROVED_PREFLIGHT", json.dumps(context.db.approval))
    assert cli.main([*context.args, "--execute"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "validation"
    assert not context.api.requests


@pytest.mark.parametrize("role", ["mitbol_render_reader", "mitbol_admin_writer"])
def test_cli_rejects_web_roles_before_database_connection(recurrent_cli, monkeypatch, capsys, role):
    context = recurrent_cli
    monkeypatch.setenv("DATABASE_URL", context.db.engine.url.set(username=role).render_as_string(hide_password=False))

    def forbidden(*args, **kwargs):
        pytest.fail("Los roles web no deben abrir una conexión operativa")

    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    assert cli.main(context.args) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "validation"
    assert not context.api.requests


@pytest.mark.parametrize("host,tls", [
    ("ep-catalog-pooler.example.com", "verify-full"),
    ("ep-catalog.example.com", "require"),
])
def test_cli_rejects_cloud_pooler_or_weak_tls_without_network(recurrent_cli, monkeypatch, capsys, host, tls):
    context = recurrent_cli
    database = "mitbol_cloud_example"
    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("EXPECTED_DATABASE", database)
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", host)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://operator:SYNTHETIC_PASSWORD@{host}/{database}?sslmode={tls}")

    def forbidden(*args, **kwargs):
        pytest.fail("El destino inseguro debe rechazarse sin conexiones")

    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    args = ["--expected-database", database, "--expected-host", host, "--store-id", "test-store"]
    assert cli.main(args) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "validation"
    assert "SYNTHETIC" not in output
    assert not context.api.requests


@pytest.mark.parametrize("exception,exit_code,category", [
    (CatalogSyncBusyError, 3, "busy"),
    (LoyverseResponseError, 2, "validation"),
    (LoyverseTransientError, 4, "provider_temporary"),
    (LoyverseAPIError, 4, "provider_rejected"),
    (CatalogPersistenceError, 5, "database"),
    (RuntimeError, 6, "unexpected"),
])
def test_cli_exit_codes_and_errors_never_expose_exception_details(recurrent_cli, monkeypatch, capsys,
                                                                exception, exit_code, category):
    context = recurrent_cli
    monkeypatch.setenv("MITBOL_RECURRENT_SYNC_APPROVAL", json.dumps(context.db.approval))
    monkeypatch.setattr(cli, "execute", AsyncMock(side_effect=exception("SYNTHETIC_PRIVATE_TOKEN cookie password")))
    assert cli.main([*context.args, "--execute"]) == exit_code
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["code"] == category and report["errors"] == [category]
    assert report["writes"] is (None if exit_code in {5, 6} else False)
    assert report["verification_required"] is (exit_code in {5, 6})
    assert all(secret not in output for secret in ("SYNTHETIC", "cookie", "password"))


def test_deferred_retry_after_is_reported_as_temporary(recurrent_cli, monkeypatch, capsys):
    context = recurrent_cli

    def deferred(request, key):
        return httpx.Response(429, headers={"Retry-After": "1000"}, json={"secret": "SYNTHETIC_PRIVATE_TOKEN"})

    api = FakeCatalogAPI(context.api.snapshot, deferred)
    monkeypatch.setattr(cli, "LoyverseAPIClient", lambda **kwargs: LoyverseAPIClient(
        access_token="synthetic", max_attempts=3, transport=httpx.MockTransport(api.handle)))
    assert cli.main(context.args) == 4
    output = capsys.readouterr().out
    assert json.loads(output)["code"] == "provider_temporary"
    assert len(api.requests) == 1
    assert "SYNTHETIC" not in output

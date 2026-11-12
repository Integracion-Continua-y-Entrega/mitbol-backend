"""HU-007: API, menú y concurrencia sobre PostgreSQL aislado."""

from app.models import catalog_metadata
from copy import deepcopy
from itertools import product
from secrets import token_urlsafe
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db.database import get_db
from app.main import create_app
from app.models import Base, Product, ProductImage, ProductTag, ProductVariant, Tag
from app.services import product_availability_service as service
from app.services.catalog_errors import CatalogSyncBusyError
from app.services.catalog_transform import transform_catalog
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY, SyncService


@pytest.fixture
def backend(sync_db, commercial_snapshot, tmp_path):
    # Semilla validada sin adquirir el bloqueo del sincronizador: las pruebas
    # pueden hacer competir conexiones PostgreSQL independientes por esa clave.
    with sync_db.sessions() as db:
        sync_db.service._write(db, transform_catalog(commercial_snapshot))
        db.commit()
        variants = {row.loyverse_id: row for row in db.scalars(select(ProductVariant))}
    key = token_urlsafe(32)
    config = Settings(
        _env_file=None, database_url=sync_db.engine.url.render_as_string(hide_password=False),
        loyverse_access_token="fake", admin_enabled=True, admin_api_key=key,
        image_storage_root=tmp_path / "images",
    )
    application = create_app(config)

    def isolated_db():
        with sync_db.sessions() as db:
            yield db

    application.dependency_overrides[get_db] = isolated_db
    with TestClient(application, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        yield SimpleNamespace(
            app=application, client=client, headers={"X-Admin-Key": key}, db=sync_db,
            variant_id=variants["single"].product_variant_id,
            sibling_id=variants["double"].product_variant_id,
            product_id=variants["single"].product_id,
        )


def state(backend):
    return {name: [dict(row) for row in backend.db.connection.execute(
        select(table).order_by(*table.primary_key.columns),
    ).mappings()] for name, table in catalog_metadata.tables.items()}


def path(backend, resource):
    identifier = backend.product_id if resource == "products" else backend.variant_id
    return f"/api/admin/{resource}/{identifier}/availability"


@pytest.mark.parametrize("active,parent_local,local,commercial", list(product([False, True], repeat=4)))
def test_admin_and_public_rules_match_for_all_conditions(backend, active, parent_local, local, commercial):
    backend.db.connection.execute(update(Product).where(Product.product_id == backend.product_id).values(
        is_active=active, is_available=parent_local,
    ))
    backend.db.connection.execute(update(ProductVariant).where(
        ProductVariant.product_id == backend.product_id,
    ).values(is_available=local, is_commercially_available=commercial))
    expected = active and parent_local and local and commercial
    variant = backend.client.get(path(backend, "variants"), headers=backend.headers).json()
    assert variant == {
        "product_variant_id": backend.variant_id, "product_id": backend.product_id,
        "is_available": local, "is_commercially_available": commercial,
        "product_is_active": active, "product_is_available": parent_local, "can_order": expected,
    }
    admin_product = backend.client.get(path(backend, "products"), headers=backend.headers).json()
    assert admin_product == {
        "product_id": backend.product_id, "is_active": active,
        "is_available": parent_local, "can_order": expected,
    }
    listed = backend.client.get("/api/menu").json()["products"]
    response = backend.client.get(f"/api/menu/{backend.product_id}")
    if not active:
        assert response.status_code == 404
        assert backend.product_id not in {row["product_id"] for row in listed}
        return
    detail = response.json()["product"]
    assert detail["can_order"] is expected
    assert next(row for row in listed if row["product_id"] == backend.product_id)["can_order"] is expected
    assert all(row["can_order"] is expected for row in detail["variants"])
    assert all(option["can_order"] is expected for row in detail["variants"]
               for modifier in row["modifiers"] for option in modifier["complements"])
    assert all("is_commercially_available" not in row for row in detail["variants"])


@pytest.mark.parametrize("resource", ["products", "variants"])
def test_patch_is_persistent_idempotent_and_changes_only_target_local_field(backend, resource):
    with backend.db.sessions() as db:
        db.get(Product, backend.product_id).description = "Descripción local"
        db.add(ProductImage(product_variant_id=backend.variant_id, image_url="/media/keep.webp", display_order=0))
        db.add(ProductTag(product_variant_id=backend.variant_id, tag=Tag(name="picante")))
        db.commit()
    before = state(backend)
    table = "Products" if resource == "products" else "ProductVariants"
    primary_key = "product_id" if resource == "products" else "product_variant_id"
    identifier = backend.product_id if resource == "products" else backend.variant_id
    for value in (False, True):
        expected = deepcopy(before)
        next(row for row in expected[table] if row[primary_key] == identifier)["is_available"] = value
        first = backend.client.patch(path(backend, resource), headers=backend.headers, json={"is_available": value})
        assert first.status_code == 200
        second = backend.client.patch(path(backend, resource), headers=backend.headers, json={"is_available": value})
        assert second.json() == first.json()
        assert backend.client.get(path(backend, resource), headers=backend.headers).json() == first.json()
        assert state(backend) == expected
        detail = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
        single, double = detail["variants"]
        assert single["can_order"] is value
        assert double["can_order"] is (value if resource == "products" else True)
        assert detail["description"] == "Descripción local"
        assert single["image_url"] == "/media/keep.webp"
        assert single["tags"][0]["name"] == "picante"


@pytest.mark.parametrize("resource", ["products", "variants"])
@pytest.mark.parametrize("payload", [
    {}, {"is_available": None}, {"is_available": "false"}, {"is_available": "true"},
    {"is_available": 0}, {"is_available": 1}, {"is_available": []},
    {"is_available": False, "is_commercially_available": True},
    {"is_available": True, "is_active": True}, {"is_available": False, "price": "0.00"},
])
def test_strict_payload_never_writes(backend, resource, payload):
    before = state(backend)
    assert backend.client.patch(path(backend, resource), headers=backend.headers, json=payload).status_code == 422
    assert state(backend) == before


@pytest.mark.parametrize("resource", ["products", "variants"])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
@pytest.mark.parametrize("identifier,status", [(0, 422), (-1, 422), (2147483648, 422), (2147483647, 404), ("abc", 422)])
def test_missing_and_invalid_ids(backend, resource, method, identifier, status):
    response = backend.client.request(method, f"/api/admin/{resource}/{identifier}/availability",
                                      headers=backend.headers, json={"is_available": True})
    assert response.status_code == status


@pytest.mark.parametrize("resource", ["products", "variants"])
@pytest.mark.parametrize("commercial", [False, True])
def test_local_restore_cannot_override_commercial_restrictions(backend, resource, commercial):
    if resource == "products":
        backend.db.connection.execute(update(Product).where(Product.product_id == backend.product_id).values(is_active=False))
    backend.db.connection.execute(update(ProductVariant).where(
        ProductVariant.product_variant_id == backend.variant_id,
    ).values(is_commercially_available=commercial))
    response = backend.client.patch(path(backend, resource), headers=backend.headers, json={"is_available": True})
    assert response.status_code == 200
    assert response.json()["is_available"] is True
    assert response.json()["can_order"] is (commercial if resource == "variants" else False)


def test_product_without_variants_cannot_order(backend):
    with backend.db.sessions() as db:
        parent = db.get(Product, backend.product_id)
        empty = Product(loyverse_id="empty", name="Sin variantes", synced_at=parent.synced_at)
        db.add(empty)
        db.commit()
        identifier = empty.product_id
    response = backend.client.patch(f"/api/admin/products/{identifier}/availability", headers=backend.headers,
                                    json={"is_available": True})
    assert response.status_code == 200 and response.json()["can_order"] is False


@pytest.mark.parametrize("resource", ["products", "variants"])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_admin_security_precedes_database_access(backend, resource, method):
    def forbidden_db():
        raise AssertionError("Acceso no autorizado a PostgreSQL")
        yield
    backend.app.dependency_overrides[get_db] = forbidden_db
    assert backend.client.request(method, path(backend, resource), content=b"invalid").status_code == 401
    assert backend.client.request(method, path(backend, resource), headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert backend.client.request(method, path(backend, resource), headers={
        **backend.headers, "Origin": "http://localhost:5173",
    }).status_code == 403
    with TestClient(backend.app, base_url="http://localhost", client=("203.0.113.1", 10000)) as client:
        assert client.request(method, path(backend, resource), headers=backend.headers).status_code == 403


def test_disabled_admin_has_no_availability_routes_and_public_menu_needs_no_key(backend):
    config = Settings(_env_file=None, database_url="postgresql://unused/unused", loyverse_access_token="fake")
    app = create_app(config)
    app.dependency_overrides[get_db] = backend.app.dependency_overrides[get_db]
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 10000)) as client:
        schema = client.get("/openapi.json").json()
        assert not any(route.startswith("/api/admin") for route in schema["paths"])
        for resource in ("products", "variants"):
            assert client.get(path(backend, resource)).status_code == 404
            assert client.patch(path(backend, resource), json={"is_available": True}).status_code == 404
        assert client.get("/api/menu").status_code == 200
        assert client.get(f"/api/menu/{backend.product_id}").status_code == 200
    schema = backend.client.get("/openapi.json").json()
    for route, methods in schema["paths"].items():
        if route.endswith("/availability"):
            assert set(methods) == {"get", "patch"}
            assert all(operation["security"] == [{"LocalAdminKey": []}] for operation in methods.values())


@pytest.mark.parametrize("resource", ["products", "variants"])
def test_sync_lock_prevents_patch_without_partial_writes(backend, resource):
    before = state(backend)
    with backend.db.engine.begin() as other:
        assert other.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY)))
        response = backend.client.patch(path(backend, resource), headers=backend.headers, json={"is_available": False})
        assert response.status_code == 409
    assert state(backend) == before
    assert backend.client.patch(path(backend, resource), headers=backend.headers, json={"is_available": False}).status_code == 200


def test_patch_lock_prevents_concurrent_sync(backend, commercial_snapshot, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    contender = SyncService(sessionmaker(bind=backend.db.engine),
                            expected_database=backend.db.engine.url.database, store_id="test-store")
    original = service._variant_response
    def competing_sync(variant):
        with pytest.raises(CatalogSyncBusyError):
            contender.persist_snapshot(commercial_snapshot)
        return original(variant)
    monkeypatch.setattr(service, "_variant_response", competing_sync)
    response = backend.client.patch(path(backend, "variants"), headers=backend.headers, json={"is_available": False})
    assert response.status_code == 200 and response.json()["is_available"] is False


@pytest.mark.parametrize("exception,status", [
    (OperationalError("PRIVATE_SQL", {}, Exception("PRIVATE_PASSWORD")), 503),
    (RuntimeError("PRIVATE_PASSWORD"), 500),
])
@pytest.mark.parametrize("resource", ["products", "variants"])
def test_failed_patch_rolls_back_and_sanitizes_errors(backend, monkeypatch, caplog, exception, status, resource):
    before = state(backend)
    def fail_response(entity):
        raise exception
    monkeypatch.setattr(service, "_product_response" if resource == "products" else "_variant_response", fail_response)
    response = backend.client.patch(path(backend, resource), headers=backend.headers, json={"is_available": False})
    assert response.status_code == status
    assert state(backend) == before
    assert "PRIVATE_PASSWORD" not in response.text + caplog.text
    assert "PRIVATE_SQL" not in response.text + caplog.text


@pytest.mark.parametrize("resource,queries", [("products", 2), ("variants", 1)])
def test_availability_reads_do_not_query_enrichment_or_modifiers(backend, resource, queries):
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(backend.db.connection, "before_cursor_execute", capture)
    try:
        assert backend.client.get(path(backend, resource), headers=backend.headers).status_code == 200
    finally:
        event.remove(backend.db.connection, "before_cursor_execute", capture)
    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(selects) == queries
    assert not any(name in sql for name in ("ProductImages", "ProductTags", "ProductModifiers") for sql in selects)


@pytest.mark.parametrize("parent_local,commercial_flags,minimum,maximum", [
    (True, (False, True), "119.00", "119.00"),
    (True, (True, False), "89.00", "89.00"),
    (True, (False, False), "89.00", "119.00"),
    (False, (False, True), "89.00", "119.00"),
])
def test_ranges_use_orderable_variants_and_fallback_to_all_prices(backend, parent_local, commercial_flags, minimum, maximum):
    backend.db.connection.execute(update(Product).where(Product.product_id == backend.product_id).values(is_available=parent_local))
    for identifier, flag in zip((backend.variant_id, backend.sibling_id), commercial_flags):
        backend.db.connection.execute(update(ProductVariant).where(
            ProductVariant.product_variant_id == identifier,
        ).values(is_commercially_available=flag))
    detail = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
    listed = next(row for row in backend.client.get("/api/menu").json()["products"] if row["product_id"] == backend.product_id)
    for item in (listed, detail):
        assert (item["price_min"], item["price_max"]) == (minimum, maximum)
        assert item["has_price_range"] is (minimum != maximum)
        assert [row["price"] for row in item["variants"]] == ["89.00", "119.00"]


def test_double_stockout_and_parent_restore_preserve_individual_preferences(backend):
    before = state(backend)
    product_path = path(backend, "products")
    single_path = path(backend, "variants")
    double_path = f"/api/admin/variants/{backend.sibling_id}/availability"
    assert backend.client.get(double_path, headers=backend.headers).json()["can_order"] is True
    response = backend.client.patch(double_path, headers=backend.headers, json={"is_available": False})
    assert response.status_code == 200
    assert response.json()["is_available"] is False
    assert response.json()["is_commercially_available"] is True

    def assert_public(single_available, double_available, minimum, maximum):
        listed = next(row for row in backend.client.get("/api/menu").json()["products"]
                      if row["product_id"] == backend.product_id)
        detail = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
        expected = {backend.variant_id: single_available, backend.sibling_id: double_available}
        for item in (listed, detail):
            assert item["can_order"] is any(expected.values())
            assert {row["product_variant_id"]: row["can_order"] for row in item["variants"]} == expected
            assert (item["price_min"], item["price_max"]) == (minimum, maximum)
            assert [row["price"] for row in item["variants"]] == ["89.00", "119.00"]
        for variant in detail["variants"]:
            assert all(option["can_order"] is expected[variant["product_variant_id"]]
                       for modifier in variant["modifiers"] for option in modifier["complements"])

    assert_public(True, False, "89.00", "89.00")
    assert backend.client.get(single_path, headers=backend.headers).json()["can_order"] is True
    assert backend.client.patch(product_path, headers=backend.headers, json={"is_available": False}).status_code == 200
    assert_public(False, False, "89.00", "119.00")
    assert backend.client.get(single_path, headers=backend.headers).json()["is_available"] is True
    assert backend.client.get(double_path, headers=backend.headers).json()["is_available"] is False
    assert backend.client.patch(product_path, headers=backend.headers, json={"is_available": True}).status_code == 200
    assert_public(True, False, "89.00", "89.00")
    assert backend.client.patch(double_path, headers=backend.headers, json={"is_available": True}).status_code == 200
    assert_public(True, True, "89.00", "119.00")
    assert state(backend) == before


@pytest.mark.parametrize("local", [False, True])
def test_commercial_sync_changes_reach_admin_and_public_api_without_overwriting_local(backend, commercial_snapshot, local):
    variant_path = f"/api/admin/variants/{backend.sibling_id}/availability"
    assert backend.client.patch(variant_path, headers=backend.headers, json={"is_available": local}).status_code == 200
    before = state(backend)
    for commercial in (False, True):
        snapshot = deepcopy(commercial_snapshot)
        snapshot.items[0]["variants"][1]["stores"][0]["available_for_sale"] = commercial
        backend.db.service.persist_snapshot(snapshot)
        response = backend.client.get(variant_path, headers=backend.headers)
        assert response.status_code == 200
        assert response.json()["is_available"] is local
        assert response.json()["is_commercially_available"] is commercial
        assert response.json()["can_order"] is (local and commercial)
        listed = next(row for row in backend.client.get("/api/menu").json()["products"]
                      if row["product_id"] == backend.product_id)
        detail = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
        for item in (listed, detail):
            single, double = item["variants"]
            assert single["can_order"] is True
            assert double["can_order"] is (local and commercial)
            assert (item["price_min"], item["price_max"]) == (
                "89.00", "119.00" if local and commercial else "89.00",
            )
        assert all(option["can_order"] is (local and commercial)
                   for modifier in detail["variants"][1]["modifiers"] for option in modifier["complements"])
        expected = deepcopy(before)
        next(row for row in expected["ProductVariants"]
             if row["product_variant_id"] == backend.sibling_id)["is_commercially_available"] = commercial
        assert state(backend) == expected


@pytest.mark.parametrize("resource", ["products", "variants"])
def test_real_postgresql_failure_rolls_back_patch_and_releases_catalog_lock(backend, monkeypatch, caplog, resource):
    before = state(backend)
    def fail_after_flush(entity):
        # Provoca un error real del servidor dentro del SAVEPOINT de la petición.
        backend.db.connection.exec_driver_sql("SELECT 1 / 0")
    with monkeypatch.context() as patch:
        patch.setattr(service, "_product_response" if resource == "products" else "_variant_response", fail_after_flush)
        response = backend.client.patch(path(backend, resource), headers=backend.headers,
                                        json={"is_available": False})
    assert response.status_code == 500
    assert response.json() == {"detail": "No fue posible completar la operación."}
    assert state(backend) == before
    assert "SELECT 1 / 0" not in response.text + caplog.text
    assert backend.headers["X-Admin-Key"] not in response.text + caplog.text
    with backend.db.engine.begin() as other:
        assert other.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY)))
    assert backend.client.patch(path(backend, resource), headers=backend.headers,
                                json={"is_available": False}).status_code == 200


def test_availability_openapi_documents_strict_input_and_matching_get_patch_responses(backend):
    assert backend.client.get("/docs").status_code == 200
    response = backend.client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    payload = schema["components"]["schemas"]["AvailabilityUpdate"]
    assert payload["additionalProperties"] is False
    assert payload["required"] == ["is_available"]
    assert payload["properties"] == {"is_available": {"type": "boolean", "title": "Is Available"}}
    for resource, parameter, model in (
        ("products", "product_id", "ProductAvailabilityResponse"),
        ("variants", "variant_id", "VariantAvailabilityResponse"),
    ):
        operations = schema["paths"][f"/api/admin/{resource}/{{{parameter}}}/availability"]
        assert set(operations) == {"get", "patch"}
        for operation in operations.values():
            assert operation["security"] == [{"LocalAdminKey": []}]
            assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{model}",
            }
            assert {"401", "403", "404", "409", "422", "500", "503"} <= operation["responses"].keys()
        assert operations["patch"]["requestBody"]["required"] is True
        assert operations["patch"]["requestBody"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/AvailabilityUpdate",
        }
    assert backend.headers["X-Admin-Key"] not in response.text
    for public_path in ("/api/menu", "/api/menu/{product_id}"):
        assert not schema["paths"][public_path]["get"].get("security")

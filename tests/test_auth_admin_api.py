"""HU-005/HU-007 reales con sesiones, PostgreSQL y archivos temporales."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from threading import Event

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.exc import OperationalError

from test_auth_persistence import auth_database
from test_auth_api import backend as auth_backend, password_hash, login, csrf_headers, token_cookie
from app.core.auth_http import cookie_name
from app.core.security import MAX_ADMIN_BODY_BYTES
from app.db.database import get_db
from app.models import AccountSession, AuditEvent, Base, Product, ProductVariant
from app.repositories import auth as repo
from app.services import product_enrichment_service as enrichment
from app.services.product_availability_service import CATALOG_SYNC_LOCK_KEY


@pytest.fixture
def backend(auth_backend, tmp_path):
    b = auth_backend
    with b.sessions.begin() as db:
        product = Product(loyverse_id="synthetic-food", name="Comida sintética", description="Original",
                          is_active=True, is_available=True, synced_at=b.now.value)
        other = Product(loyverse_id="synthetic-other", name="Otro producto", is_active=True,
                        is_available=True, synced_at=b.now.value)
        variant = ProductVariant(product=product, loyverse_id="synthetic-small", name="Individual",
                                 price="99.50", is_available=True, is_commercially_available=True,
                                 synced_at=b.now.value)
        sibling = ProductVariant(product=product, loyverse_id="synthetic-large", name="Grande",
                                 price="149.50", is_available=True, is_commercially_available=True,
                                 synced_at=b.now.value)
        foreign = ProductVariant(product=other, loyverse_id="synthetic-foreign", name="Otra",
                                 price="49.50", is_available=True, is_commercially_available=False,
                                 synced_at=b.now.value)
        db.add_all([variant, sibling, foreign])
        db.flush()
        b.product_id, b.variant_id = product.product_id, variant.product_variant_id
        b.sibling_id, b.foreign_id = sibling.product_variant_id, foreign.product_variant_id

    def public_db():
        with b.sessions() as db:
            yield db

    b.app.dependency_overrides[get_db] = public_db
    b.storage = b.app.state.image_storage
    b.storage.root = (tmp_path / "images").resolve()
    yield b
    with b.sessions.begin() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))


def product_path(b, field="description"):
    return f"/api/admin/products/{b.product_id}/{field}"


def variant_path(b, suffix="availability"):
    return f"/api/admin/variants/{b.variant_id}/{suffix}"


def catalog_state(b):
    with b.sessions() as db:
        return {name: [dict(row) for row in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
                for name, table in Base.metadata.tables.items()}


def audit_rows(b):
    with b.sessions() as db:
        return list(db.scalars(select(AuditEvent).where(AuditEvent.action.like("catalog.%"))
                               .order_by(AuditEvent.audit_event_id)))


def picture():
    output = BytesIO()
    Image.new("RGB", (10, 8), "red").save(output, format="PNG")
    return output.getvalue()


def upload(b, headers, content=None, **kwargs):
    return b.client.post(variant_path(b, "images"), headers=headers,
                         files={"file": ("untrusted.png", picture() if content is None else content, "image/png")},
                         **kwargs)


def test_admin_description_and_public_contract_preserve_commercial_fields(backend):
    b = backend
    before = catalog_state(b)
    response = b.client.patch(product_path(b), headers=csrf_headers(login(b)), json={"description": " Texto local "})
    assert response.status_code == 200 and response.json() == {"product_id": b.product_id, "description": "Texto local"}
    assert response.headers["cache-control"] == "no-store"
    expected = deepcopy(before)
    next(row for row in expected["Products"] if row["product_id"] == b.product_id)["description"] = "Texto local"
    assert catalog_state(b) == expected
    public = b.client.get(f"/api/menu/{b.product_id}").json()["product"]
    assert public["description"] == "Texto local"
    row, = audit_rows(b)
    assert (row.action, row.actor_account_id, row.resource_type, row.resource_id, row.outcome) == (
        "catalog.product.description", b.ids["admin"][0], "product", str(b.product_id), "success")
    assert row.request_id and row.failure_code is None


@pytest.mark.parametrize("role", ["admin", "staff"])
@pytest.mark.parametrize("resource", ["products", "variants"])
def test_both_roles_change_only_local_availability_and_public_menu_reflects_it(backend, role, resource):
    b = backend
    before = catalog_state(b)
    headers = csrf_headers(login(b, role))
    path = product_path(b, "availability") if resource == "products" else variant_path(b)
    assert b.client.get(path).status_code == 200
    response = b.client.patch(path, headers=headers, json={"is_available": False})
    assert response.status_code == 200 and response.json()["can_order"] is False
    expected = deepcopy(before)
    table, key, identifier = (("Products", "product_id", b.product_id) if resource == "products"
                              else ("ProductVariants", "product_variant_id", b.variant_id))
    next(row for row in expected[table] if row[key] == identifier)["is_available"] = False
    assert catalog_state(b) == expected
    detail = b.client.get(f"/api/menu/{b.product_id}").json()["product"]
    assert detail["can_order"] is (resource == "variants")  # La otra variante sigue disponible.
    assert next(v for v in detail["variants"] if v["product_variant_id"] == b.variant_id)["can_order"] is False
    listed = b.client.get("/api/menu").json()["products"]
    assert next(p for p in listed if p["product_id"] == b.product_id)["can_order"] is detail["can_order"]
    assert b.client.patch(path, headers=headers, json={"is_available": True}).status_code == 200
    assert catalog_state(b) == before
    assert all(row.actor_account_id == b.ids[role][0] for row in audit_rows(b))
    assert len(audit_rows(b)) == 2


OPERATIONS = [
    ("GET", "/products/{p}/availability", False), ("PATCH", "/products/{p}/availability", False),
    ("GET", "/variants/{v}/availability", False), ("PATCH", "/variants/{v}/availability", False),
    ("GET", "/products/{p}/description", False), ("PATCH", "/products/{p}/description", True),
    ("GET", "/variants/{v}/images", False), ("POST", "/variants/{v}/images", True),
    ("PATCH", "/variants/{v}/images/1", True), ("DELETE", "/variants/{v}/images/1", True),
    ("GET", "/tags", True), ("POST", "/tags", True), ("GET", "/variants/{v}/tags", True),
    ("PUT", "/variants/{v}/tags/1", True), ("DELETE", "/variants/{v}/tags/1", True),
]


@pytest.mark.parametrize("method,path,admin_only", OPERATIONS)
def test_all_real_operations_reject_anonymous_before_parsing_even_with_shared_key(backend, method, path, admin_only):
    b = backend
    response = b.client.request(method, "/api/admin" + path.format(p=b.product_id, v=b.variant_id),
        headers={"Origin": "http://localhost:5173", "X-Admin-Key": "synthetic-key-that-must-never-authorize"},
        content=b"invalid body")
    assert response.status_code == 401
    assert not audit_rows(b) and not b.storage.root.exists()


@pytest.mark.parametrize("method,path,admin_only", [op for op in OPERATIONS if op[2]])
def test_staff_is_denied_before_body_parsing_and_creates_no_mutation(backend, method, path, admin_only):
    b = backend
    headers = csrf_headers(login(b, "staff"))
    before = catalog_state(b)
    response = b.client.request(method, "/api/admin" + path.format(p=b.product_id, v=b.variant_id),
                                headers=headers, content=b"invalid multipart or JSON")
    assert response.status_code == 403
    assert catalog_state(b) == before and not audit_rows(b) and not b.storage.root.exists()
    with b.sessions() as db:
        denial = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.access"))
        assert denial.actor_account_id == b.ids["staff"][0] and denial.failure_code == "INSUFFICIENT_PERMISSION"


@pytest.mark.parametrize("headers_mode", ["missing_csrf", "invalid_csrf", "missing_origin", "evil_origin", "null_origin"])
@pytest.mark.parametrize("operation", ["description", "availability", "image", "tag", "attach", "delete_image"])
def test_every_mutation_requires_csrf_and_origin(backend, headers_mode, operation):
    b = backend
    headers = csrf_headers(login(b))
    if headers_mode == "missing_csrf":
        headers.pop("X-CSRF-Token")
    elif headers_mode == "invalid_csrf":
        headers["X-CSRF-Token"] = "invalid"
    elif headers_mode == "missing_origin":
        headers.pop("Origin")
    else:
        headers["Origin"] = "null" if headers_mode == "null_origin" else "https://evil.example"
    before = catalog_state(b)
    if operation == "image":
        response = upload(b, headers)
    else:
        method, path, data = {
            "description": ("PATCH", product_path(b), {"description": "Forbidden"}),
            "availability": ("PATCH", variant_path(b), {"is_available": False}),
            "tag": ("POST", "/api/admin/tags", {"name": "Forbidden"}),
            "attach": ("PUT", variant_path(b, "tags/1"), None),
            "delete_image": ("DELETE", variant_path(b, "images/1"), None),
        }[operation]
        response = b.client.request(method, path, headers=headers, json=data)
    assert response.status_code == 403
    assert catalog_state(b) == before and not audit_rows(b) and not b.storage.root.exists()


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_expired_or_revoked_session_cannot_mutate(backend, state):
    b = backend
    headers = csrf_headers(login(b))
    if state == "expired":
        b.now.value += timedelta(minutes=31)
    else:
        with b.sessions.begin() as db:
            db.execute(update(AccountSession).values(revoked_at=b.now.value, revocation_reason="synthetic"))
    before = catalog_state(b)
    assert b.client.patch(product_path(b), headers=headers, json={"description": "Forbidden"}).status_code == 401
    assert catalog_state(b) == before and not audit_rows(b)


def test_tags_normalization_validation_relations_idempotence_and_audits(backend):
    b = backend
    headers = csrf_headers(login(b))
    for invalid in ["", "x" * 151, "<b>HTML</b>"]:
        assert b.client.post("/api/admin/tags", headers=headers, json={"name": invalid}).status_code == 422
    response = b.client.post("/api/admin/tags", headers=headers, json={"name": "  PICANTE   local  "})
    assert response.status_code == 201
    tag = response.json()
    assert tag["name"] == "picante local"
    assert b.client.post("/api/admin/tags", headers=headers, json={"name": "picante local"}).status_code == 409
    path = variant_path(b, f"tags/{tag['tag_id']}")
    for _ in range(2):
        assert b.client.put(path, headers=headers).status_code == 204
    assert b.client.get(variant_path(b, "tags")).json() == {"tags": [tag]}
    assert b.client.get("/api/admin/tags").json() == {"tags": [tag]}
    detail = b.client.get(f"/api/menu/{b.product_id}").json()["product"]
    assert detail["variants"][0]["tags"] == [{"tag_id": tag["tag_id"], "name": tag["name"]}]
    for _ in range(2):
        assert b.client.delete(path, headers=headers).status_code == 204
    assert b.client.put(variant_path(b, "tags/2147483647"), headers=headers).status_code == 404
    assert [row.action for row in audit_rows(b)] == ["catalog.tag.create", "catalog.variant.tag.attach",
        "catalog.variant.tag.attach", "catalog.variant.tag.remove", "catalog.variant.tag.remove"]
    assert audit_rows(b)[1].resource_id == f"{b.variant_id}:{tag['tag_id']}"


def test_images_keep_existing_validation_references_media_and_audit(backend):
    b = backend
    headers = csrf_headers(login(b))
    assert upload(b, headers, b"not an image").status_code == 415
    assert upload(b, headers, b"\x89PNGbroken").status_code == 422
    assert upload(b, headers, b"x" * 5_000_001).status_code == 413
    assert upload(b, headers, data={"display_order": "-1"}).status_code == 422
    response = upload(b, headers)
    assert response.status_code == 201
    image = response.json()
    path = variant_path(b, f"images/{image['product_image_id']}")
    assert b.client.get(image["image_url"]).status_code == 200
    assert b.client.get(variant_path(b, "images")).json() == {"images": [image]}
    foreign = f"/api/admin/variants/{b.foreign_id}/images/{image['product_image_id']}"
    assert b.client.delete(foreign, headers=headers).status_code == 404
    assert b.client.patch(path, headers=headers, json={"display_order": -1}).status_code == 422
    assert b.client.patch(path, headers=headers, json={"display_order": 2}).json()["display_order"] == 2
    assert b.client.delete(path, headers=headers).status_code == 204
    assert not b.storage.path_for(image["image_url"]).exists()
    assert b.client.get(image["image_url"]).status_code == 404
    assert not list(b.storage.root.glob(".pending/*"))
    assert [r.action for r in audit_rows(b)] == ["catalog.image.create", "catalog.image.order", "catalog.image.delete"]


@pytest.mark.parametrize("operation", ["description", "product_availability", "variant_availability", "tag", "attach", "remove", "upload", "order", "delete"])
def test_audit_failure_rolls_back_every_real_mutation_and_image_side_effects(backend, operation):
    b = backend
    headers = csrf_headers(login(b))
    image = upload(b, headers).json()
    tag = b.client.post("/api/admin/tags", headers=headers, json={"name": "existing"}).json()
    association = variant_path(b, f"tags/{tag['tag_id']}")
    if operation == "remove":
        assert b.client.put(association, headers=headers).status_code == 204
    before, audit_before = catalog_state(b), len(audit_rows(b))

    def fail(mapper, connection, target):
        if target.action.startswith("catalog."):
            raise OperationalError("PRIVATE SQL", {}, Exception("PRIVATE PASSWORD"))

    event.listen(AuditEvent, "before_insert", fail)
    try:
        if operation == "upload":
            response = upload(b, headers)
        else:
            method, path, payload = {
                "description": ("PATCH", product_path(b), {"description": "Rollback"}),
                "product_availability": ("PATCH", product_path(b, "availability"), {"is_available": False}),
                "variant_availability": ("PATCH", variant_path(b), {"is_available": False}),
                "tag": ("POST", "/api/admin/tags", {"name": "rollback"}),
                "attach": ("PUT", association, None), "remove": ("DELETE", association, None),
                "order": ("PATCH", variant_path(b, f"images/{image['product_image_id']}"), {"display_order": 99}),
                "delete": ("DELETE", variant_path(b, f"images/{image['product_image_id']}"), None),
            }[operation]
            response = b.client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 503
        assert "PRIVATE" not in response.text
    finally:
        event.remove(AuditEvent, "before_insert", fail)
    assert catalog_state(b) == before and len(audit_rows(b)) == audit_before
    assert b.storage.path_for(image["image_url"]).is_file()
    assert len(list(b.storage.root.rglob("*.png"))) == 1
    assert not list(b.storage.root.glob(".pending/*"))


def test_auth_and_mutation_are_rechecked_after_body_reception(backend, monkeypatch):
    b = backend
    headers = csrf_headers(login(b))
    original = b.service._resolve
    count = 0

    def resolve(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            b.now.value += timedelta(minutes=31)
        return original(*args, **kwargs)

    monkeypatch.setattr(b.service, "_resolve", resolve)
    assert b.client.patch(product_path(b), headers=headers, json={"description": "Forbidden"}).status_code == 401
    assert not audit_rows(b)
    with b.sessions() as db:
        assert db.get(Product, b.product_id).description == "Original"


def test_expiration_during_image_mutation_rolls_back_reference_audit_and_file(backend, monkeypatch):
    b = backend
    headers = csrf_headers(login(b))
    original = enrichment.add_image

    def expiring(*args, **kwargs):
        result = original(*args, **kwargs)
        b.now.value += timedelta(minutes=31)
        return result

    monkeypatch.setattr(enrichment, "add_image", expiring)
    before = catalog_state(b)
    assert upload(b, headers).status_code == 401
    assert catalog_state(b) == before and not audit_rows(b)
    assert not list(b.storage.root.rglob("*.png")) and not list(b.storage.root.glob(".pending/*"))


@pytest.mark.parametrize("operation", ["upload", "delete"])
def test_uncertain_commit_recovers_files_from_committed_database_truth(backend, operation):
    b = backend
    headers = csrf_headers(login(b))
    image = upload(b, headers).json() if operation == "delete" else None
    session_class = b.app.state.auth_session_factory.class_

    def uncertain(session):
        if session.info.get("admin_image_storages"):
            raise OperationalError("PRIVATE uncertain commit", {}, Exception("PRIVATE"))

    event.listen(session_class, "after_commit", uncertain)
    try:
        response = (upload(b, headers) if operation == "upload" else
                    b.client.delete(variant_path(b, f"images/{image['product_image_id']}"), headers=headers))
        assert response.status_code == 503 and "PRIVATE" not in response.text
    finally:
        event.remove(session_class, "after_commit", uncertain)
    images = b.client.get(variant_path(b, "images")).json()["images"]
    if operation == "upload":
        assert len(images) == 1 and b.storage.path_for(images[0]["image_url"]).is_file()
        assert len(audit_rows(b)) == 1
    else:
        assert images == [] and not b.storage.path_for(image["image_url"]).exists()
        assert len(audit_rows(b)) == 2
    assert not list(b.storage.root.glob(".pending/*"))


def test_size_guard_and_cors_preflight_use_session_mode(backend):
    b = backend
    headers = csrf_headers(login(b))
    preflight = b.client.options(variant_path(b, "images"), headers={"Origin": headers["Origin"],
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-CSRF-Token"})
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"
    response = b.client.post(variant_path(b, "images"), headers=headers, content=b"x" * (MAX_ADMIN_BODY_BYTES + 1))
    assert response.status_code == 413 and not b.storage.root.exists()
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    assert "LocalAdminKey" not in str(b.client.get("/openapi.json").json())


def test_catalog_lock_conflict_changes_neither_data_nor_audit(backend):
    b = backend
    headers = csrf_headers(login(b, "staff"))
    before = catalog_state(b)
    with b.sessions.begin() as other:
        other.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CATALOG_SYNC_LOCK_KEY})
        response = b.client.patch(variant_path(b), headers=headers, json={"is_available": False})
    assert response.status_code == 409
    assert catalog_state(b) == before and not audit_rows(b)


@pytest.mark.parametrize("field,value", [("price", 1), ("is_active", False),
    ("is_commercially_available", False), ("loyverse_id", "overwrite")])
def test_authenticated_staff_cannot_update_commercial_fields(backend, field, value):
    b = backend
    before = catalog_state(b)
    response = b.client.patch(variant_path(b), headers=csrf_headers(login(b, "staff")),
                              json={"is_available": False, field: value})
    assert response.status_code == 422 and catalog_state(b) == before and not audit_rows(b)


def test_two_uploads_serialize_with_unique_order_and_atomic_audits(backend):
    b = backend
    headers = csrf_headers(login(b))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload(b, headers), range(2)))
    # La recuperación posterior al commit también usa el lock no bloqueante.
    # Un conflicto 409 es válido; al reintentar no debe duplicar filas/auditoría.
    assert any(r.status_code == 201 for r in results)
    assert all(r.status_code in {201, 409} for r in results)
    for result in results:
        if result.status_code == 409:
            assert upload(b, headers).status_code == 201
    images = b.client.get(variant_path(b, "images")).json()["images"]
    assert [image["display_order"] for image in images] == [0, 1]
    assert len({image["image_url"] for image in images}) == 2
    assert all(b.storage.path_for(image["image_url"]).is_file() for image in images)
    assert len(audit_rows(b)) == 2 and not list(b.storage.root.glob(".pending/*"))


def test_two_roles_competing_for_availability_return_conflict_without_partial_write(backend, monkeypatch):
    from app.services import product_availability_service as availability

    b = backend
    admin_headers = csrf_headers(login(b))
    staff = TestClient(b.app, base_url="http://localhost:8000", client=("127.0.0.1", 54330))
    staff_headers = csrf_headers(login(b, "staff", client=staff))
    updating, release = Event(), Event()
    original = availability._product

    def paused(*args):
        updating.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(availability, "_product", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(b.client.patch, product_path(b, "availability"), headers=admin_headers,
                            json={"is_available": False})
        try:
            assert updating.wait(5)
            second = staff.patch(variant_path(b), headers=staff_headers, json={"is_available": False})
            assert second.status_code == 409
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
    staff.close()
    with b.sessions() as db:
        assert db.get(Product, b.product_id).is_available is False
        assert db.get(ProductVariant, b.variant_id).is_available is True
    row, = audit_rows(b)
    assert row.actor_account_id == b.ids["admin"][0]


def test_all_real_admin_operations_are_inaccessible_in_cloud_without_connecting(monkeypatch, tmp_path):
    from sqlalchemy.engine import Engine
    from test_cloud_configuration import cloud_settings
    from app.main import create_app

    def forbidden(*args, **kwargs):
        pytest.fail("Cloud intentó conectar una ruta administrativa bloqueada")

    monkeypatch.setattr(Engine, "connect", forbidden)
    app = create_app(cloud_settings(image_storage_root=tmp_path / "images"))
    with TestClient(app) as client:
        for method, path, _ in OPERATIONS:
            response = client.request(method, "/api/admin" + path.format(p=1, v=1),
                headers={"X-Admin-Key": "synthetic", "X-CSRF-Token": "synthetic"}, content=b"invalid")
            assert response.status_code == 404
        assert not any(path.startswith(("/api/admin", "/api/auth")) for path in client.get("/openapi.json").json()["paths"])
    assert not (tmp_path / "images").exists()


def test_mutation_and_security_revocation_serialize_on_account_lock(backend, monkeypatch):
    b = backend
    headers = csrf_headers(login(b))
    token = token_cookie(b)
    updating, release, revoking = Event(), Event(), Event()
    original = enrichment.update_description

    def paused(*args, **kwargs):
        updating.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(enrichment, "update_description", paused)

    def change():
        return b.client.patch(product_path(b), headers=headers, json={"description": "Serialized"})

    def revoke():
        with b.sessions.begin() as db:
            revoking.set()
            account = repo.lock_account(db, b.ids["admin"][0])
            repo.invalidate_locked_account(db, account, b.now.value, "synthetic_concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = pool.submit(change)
        assert updating.wait(5)
        revoked = pool.submit(revoke)
        assert revoking.wait(5)
        release.set()
        assert changed.result(timeout=10).status_code == 200
        revoked.result(timeout=10)
    b.client.cookies.set(cookie_name(b.config), token)
    assert b.client.patch(product_path(b), headers=headers, json={"description": "Forbidden"}).status_code == 401
    with b.sessions() as db:
        assert db.get(Product, b.product_id).description == "Serialized"
    assert len(audit_rows(b)) == 1

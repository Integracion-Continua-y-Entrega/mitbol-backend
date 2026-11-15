"""Endpoints reales, PostgreSQL desechable y R2 simulado compartido entre reinicios."""

from botocore.exceptions import ReadTimeoutError
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from test_enrichment_api import (
    backend as local_backend, image_rows, install_commit_failure, upload,
)
from test_auth_admin_api import (
    backend as session_backend, auth_backend, auth_database, password_hash,
    audit_rows, upload as authenticated_upload, variant_path,
)
from test_auth_api import csrf_headers, login
from test_r2_image_storage import FakeS3, r2
from app.models import AuditEvent, Product, ProductImage


@pytest.fixture
def object_backend(local_backend):
    b = local_backend
    b.storage = r2()
    b.app.state.image_storage = b.storage
    return b


@pytest.fixture
def authenticated_backend(session_backend):
    b = session_backend
    b.storage = r2()
    b.app.state.image_storage = b.storage
    return b


def image_path(b, image):
    return f"/api/admin/variants/{b.variant_id}/images/{image['product_image_id']}"


def test_upload_gallery_order_read_restart_and_delete_keep_the_contract(object_backend):
    b = object_backend
    first_response = upload(b)
    assert first_response.status_code == 201
    first = first_response.json()
    assert set(first) == {"product_image_id", "product_variant_id", "image_url", "display_order", "created_at"}
    second = upload(b).json()
    assert first["image_url"].startswith(f"/media/products/{b.product_id}/variants/{b.variant_id}/")
    assert b.client.patch(image_path(b, second), headers=b.headers, json={"display_order": 0}).status_code == 200
    assert b.client.patch(image_path(b, first), headers=b.headers, json={"display_order": 2}).status_code == 200
    detail = b.client.get(f"/api/menu/{b.product_id}").json()["product"]
    assert detail["image_url"] == second["image_url"]
    assert [image["product_image_id"] for image in detail["variants"][0]["images"]] == [second["product_image_id"], first["product_image_id"]]
    original = b.client.get(first["image_url"])
    assert original.status_code == 200 and original.headers["content-type"] == "image/png"
    assert original.headers["x-content-type-options"] == "nosniff"
    assert int(original.headers["content-length"]) == len(original.content)
    b.storage = r2(b.storage._client)
    b.app.state.image_storage = b.storage
    assert b.client.get(first["image_url"]).content == original.content
    assert b.client.delete(image_path(b, first), headers=b.headers).status_code == 204
    assert b.client.get(first["image_url"]).status_code == 404
    assert image_rows(b) == 1
    assert list(b.storage._client.objects) == [b.storage.key_for(second["image_url"])]


def test_publication_rules_are_checked_before_accessing_private_bucket(object_backend):
    b = object_backend
    image = upload(b).json()
    b.storage._client.calls.clear()
    url = image["image_url"].replace(image["image_url"].rsplit("/", 1)[1], "d" * 32 + ".png")
    assert b.client.get(url).status_code == 404
    with b.db.sessions.begin() as db:
        db.execute(update(Product).where(Product.product_id == b.product_id).values(is_active=False))
    assert b.client.get(image["image_url"]).status_code == 404
    assert b.client.get("/media/.pending/private.json").status_code == 404
    assert not b.storage._client.calls


def test_missing_object_and_provider_errors_remain_distinct_and_sanitized(object_backend, caplog):
    b = object_backend
    image = upload(b).json()
    key = b.storage.key_for(image["image_url"])
    saved = b.storage._client.objects.pop(key)
    assert b.client.get(image["image_url"]).status_code == 404
    assert image_rows(b) == 1
    b.storage._client.objects[key] = saved
    b.storage._client.fail = lambda *args: (_ for _ in ()).throw(FakeS3.error())
    response = b.client.get(image["image_url"])
    assert response.status_code == 503
    assert "PRIVATE" not in response.text + caplog.text and "synthetic-private-secret" not in response.text + caplog.text


@pytest.mark.parametrize("content,status", [(b"not-image", 415), (b"\x89PNGbroken", 422), (b"x" * 5_000_001, 413)],
                         ids=["unsupported", "corrupt", "oversized"])
def test_invalid_files_never_contact_r2(object_backend, content, status):
    b = object_backend
    assert upload(b, content).status_code == status
    assert not b.storage._client.calls and image_rows(b) == 0


@pytest.mark.parametrize("operation", ["upload", "delete"])
@pytest.mark.parametrize("after_commit", [False, True])
def test_database_commit_failure_or_uncertainty_is_reconciled_against_actual_rows(object_backend, monkeypatch, operation, after_commit):
    b = object_backend
    image = upload(b).json() if operation == "delete" else None
    install_commit_failure(b, monkeypatch, after_commit=after_commit)
    response = upload(b) if operation == "upload" else b.client.delete(image_path(b, image), headers=b.headers)
    assert response.status_code == 503
    rows = b.db.connection.execute(select(ProductImage.__table__)).mappings().all()
    expected = int(after_commit if operation == "upload" else not after_commit)
    assert len(rows) == expected
    assert len(b.storage._client.objects) == expected
    for row in rows:
        assert b.client.get(row["image_url"]).status_code == 200


@pytest.mark.parametrize("phase", ["journal", "staging", "final"])
def test_provider_write_failure_never_commits_a_reference_and_preserves_recovery_intent(object_backend, phase):
    b = object_backend

    def fail(method, kwargs):
        key = kwargs.get("Key", "")
        target = "/.pending/" if phase == "journal" else "/.staging/" if phase == "staging" else "/variants/"
        if method == "put" and target in key:
            raise ReadTimeoutError(endpoint_url="PRIVATE")
    b.storage._client.fail = fail
    assert upload(b).status_code == 503
    assert image_rows(b) == 0
    assert not any("/variants/" in key for key in b.storage._client.objects)
    if phase != "journal":
        assert any("/.pending/" in key for key in b.storage._client.objects)


def test_failed_object_delete_is_private_and_recovered_on_next_mutation(object_backend):
    b = object_backend
    image = upload(b).json()
    key = b.storage.key_for(image["image_url"])

    def fail(method, kwargs):
        if method == "delete" and kwargs["Key"] == key:
            raise FakeS3.error()
    b.storage._client.fail = fail
    assert b.client.delete(image_path(b, image), headers=b.headers).status_code == 204
    assert image_rows(b) == 0 and key in b.storage._client.objects
    assert b.client.get(image["image_url"]).status_code == 404
    b.storage._client.fail = None
    assert upload(b).status_code == 201
    assert key not in b.storage._client.objects
    assert not any("/.pending/" in key for key in b.storage._client.objects)


def test_shared_reference_is_not_physically_deleted(object_backend):
    b = object_backend
    image = upload(b).json()
    with b.db.sessions.begin() as db:
        db.add(ProductImage(product_variant_id=b.variant_id, image_url=image["image_url"], display_order=3))
    assert b.client.delete(image_path(b, image), headers=b.headers).status_code == 204
    assert b.client.get(image["image_url"]).status_code == 200 and image_rows(b) == 1


@pytest.mark.parametrize("case,status", [("anonymous", 401), ("csrf", 403), ("origin", 403), ("staff", 403)])
def test_session_security_denies_before_touching_r2(authenticated_backend, case, status):
    b = authenticated_backend
    headers = {"Origin": "http://localhost:5173"} if case == "anonymous" else csrf_headers(login(b, "staff" if case == "staff" else "admin"))
    if case == "csrf":
        headers.pop("X-CSRF-Token")
    if case == "origin":
        headers["Origin"] = "https://unapproved.example"
    assert authenticated_upload(b, headers).status_code == status
    assert not b.storage._client.calls


def test_authenticated_upload_read_order_delete_and_atomic_audits(authenticated_backend):
    b = authenticated_backend
    headers = csrf_headers(login(b))
    response = authenticated_upload(b, headers)
    assert response.status_code == 201
    image = response.json()
    path = variant_path(b, f"images/{image['product_image_id']}")
    assert b.client.get(image["image_url"]).status_code == 200
    assert b.client.patch(path, headers=headers, json={"display_order": 4}).status_code == 200
    assert b.client.delete(path, headers=headers).status_code == 204
    assert not b.storage._client.objects
    assert [row.action for row in audit_rows(b)] == ["catalog.image.create", "catalog.image.order", "catalog.image.delete"]


@pytest.mark.parametrize("operation", ["upload", "delete"])
def test_failed_audit_rolls_back_object_changes(authenticated_backend, operation):
    b = authenticated_backend
    headers = csrf_headers(login(b))
    image = authenticated_upload(b, headers).json() if operation == "delete" else None

    def fail_audit(mapper, connection, row):
        if row.action.startswith("catalog."):
            raise OperationalError("PRIVATE", {}, Exception("PRIVATE"))
    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        response = (authenticated_upload(b, headers) if operation == "upload" else
                    b.client.delete(variant_path(b, f"images/{image['product_image_id']}"), headers=headers))
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)
    assert response.status_code == 503
    assert len(b.storage._client.objects) == (1 if operation == "delete" else 0)
    if image:
        assert b.client.get(image["image_url"]).status_code == 200

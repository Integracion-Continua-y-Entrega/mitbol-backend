"""Bloque 2: API HU-005 con PostgreSQL aislado y archivos temporales."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from secrets import token_urlsafe
from types import SimpleNamespace
import struct
import zlib

from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.db.database import get_db
from app.main import create_app
from app.models import Product, ProductImage, ProductTag, ProductVariant, Tag
from app.services import product_enrichment_service as service
from app.services.enrichment_errors import StorageError
from app.services.image_storage import LocalImageStorage, MAX_IMAGE_BYTES


def picture(format_name="PNG", *, animated=False, metadata=False):
    output = BytesIO()
    image = Image.new("RGB", (12, 8), "red")
    options = {}
    if animated:
        options = {"save_all": True, "append_images": [Image.new("RGB", image.size, "blue")], "duration": 100}
    if metadata:
        exif = Image.Exif()
        exif[315] = "private metadata"
        options["exif"] = exif
    image.save(output, format=format_name, **options)
    return output.getvalue()


@pytest.fixture
def backend(sync_db, commercial_snapshot, tmp_path):
    sync_db.service.persist_snapshot(commercial_snapshot)
    with sync_db.sessions() as session:
        variants = {variant.loyverse_id: variant for variant in session.scalars(select(ProductVariant))}
    key = token_urlsafe(32)
    config = Settings(
        _env_file=None, database_url=sync_db.engine.url.render_as_string(hide_password=False),
        loyverse_access_token="fake-loyverse", admin_enabled=True,
        admin_api_key=key, image_storage_root=tmp_path / "images",
    )
    application = create_app(config)

    def isolated_db():
        with sync_db.sessions() as session:
            yield session

    application.dependency_overrides[get_db] = isolated_db
    with TestClient(application, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        yield SimpleNamespace(
            app=application, client=client, headers={"X-Admin-Key": key}, key=key,
            db=sync_db, variants=variants, storage=application.state.image_storage,
            variant_id=variants["single"].product_variant_id,
            other_id=variants["double"].product_variant_id,
            product_id=variants["single"].product_id,
        )


def upload(backend, content=None, *, variant_id=None, order=None, filename="untrusted.png", mime="image/png"):
    return backend.client.post(
        f"/api/admin/variants/{variant_id or backend.variant_id}/images",
        headers=backend.headers,
        files={"file": (filename, picture() if content is None else content, mime)},
        data={} if order is None else {"display_order": str(order)},
    )


def image_rows(backend):
    return backend.db.connection.scalar(select(func.count()).select_from(ProductImage))


def pending_files(backend):
    return list(backend.storage.root.glob(".pending/*"))


@pytest.mark.parametrize("enabled,key_mode", [(True, "missing"), (True, "short"), (True, "same_as_loyverse")])
def test_invalid_admin_configuration_fails_without_printing_secrets(enabled, key_mode):
    secret = token_urlsafe(32)
    key = {"missing": None, "short": "short", "same_as_loyverse": secret}[key_mode]
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url="postgresql://example/unused", loyverse_access_token=secret,
                 admin_enabled=enabled, admin_api_key=key)
    assert secret not in str(error.value)


def test_admin_is_disabled_by_default_and_has_no_routes_or_storage(tmp_path):
    config = Settings(_env_file=None, database_url="postgresql://example/unused", loyverse_access_token="fake",
                      image_storage_root=tmp_path / "images")
    assert not config.admin_enabled
    application = create_app(config)
    with TestClient(application, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/admin/tags").status_code == 404
        schema = client.get("/openapi.json").json()
        assert not any(path.startswith("/api/admin") for path in schema["paths"])
        assert client.get("/").status_code == 200
    assert not config.image_storage_root.exists()


def test_app_factory_rejects_invalid_configuration_even_after_model_copy():
    config = Settings(_env_file=None, database_url="postgresql://example/unused", loyverse_access_token="fake")
    config = config.model_copy(update={"admin_enabled": True})
    with pytest.raises(ValueError):
        create_app(config)


@pytest.mark.parametrize("method,path", [
    ("GET", "/products/1/description"), ("PATCH", "/products/1/description"),
    ("GET", "/variants/1/images"), ("POST", "/variants/1/images"),
    ("PATCH", "/variants/1/images/1"), ("DELETE", "/variants/1/images/1"),
    ("GET", "/tags"), ("POST", "/tags"), ("GET", "/variants/1/tags"),
    ("PUT", "/variants/1/tags/1"), ("DELETE", "/variants/1/tags/1"),
])
def test_every_admin_operation_requires_a_credential(backend, method, path):
    def forbidden_db():
        raise AssertionError("Una petición sin credencial intentó acceder a PostgreSQL")
        yield
    backend.app.dependency_overrides[get_db] = forbidden_db
    response = backend.client.request(method, "/api/admin" + path, content=b"invalid body")
    assert response.status_code == 401
    assert not pending_files(backend)


@pytest.mark.parametrize("headers", [
    {"X-Admin-Key": "wrong"}, {"X-Admin-Key": "\u00e1"},
])
def test_invalid_credential_never_leaks(backend, headers, caplog):
    if not headers["X-Admin-Key"].isascii():
        headers = [(b"x-admin-key", headers["X-Admin-Key"].encode("utf-8"))]
    response = backend.client.get("/api/admin/tags", headers=headers)
    assert response.status_code == 401
    assert backend.key not in response.text + caplog.text


@pytest.mark.parametrize("extra", [
    {"Origin": "http://localhost:5173"}, {"Host": "attacker.example"},
    {"Forwarded": "for=127.0.0.1"}, {"X-Forwarded-For": "127.0.0.1"},
    {"X-Forwarded-Host": "localhost"}, {"X-Forwarded-Proto": "http"},
])
def test_admin_rejects_react_remote_hosts_and_proxies(backend, extra):
    response = backend.client.get("/api/admin/tags", headers={**backend.headers, **extra})
    assert response.status_code == 403


def test_admin_rejects_remote_client_even_with_valid_key(backend):
    with TestClient(backend.app, base_url="http://localhost", client=("203.0.113.10", 1234)) as client:
        assert client.get("/api/admin/tags", headers=backend.headers).status_code == 403


def test_swagger_documents_temporary_security_and_keeps_public_menu_open(backend):
    schema = backend.client.get("/openapi.json").json()
    scheme = schema["components"]["securitySchemes"]["LocalAdminKey"]
    assert scheme["name"] == "X-Admin-Key" and scheme["in"] == "header"
    assert "React" in scheme["description"] and "roles" in scheme["description"]
    assert backend.key not in str(schema)
    assert not schema["paths"]["/api/menu"]["get"].get("security")
    for path, methods in schema["paths"].items():
        if path.startswith("/api/admin"):
            assert all(operation["security"] == [{"LocalAdminKey": []}] for operation in methods.values())
    assert "multipart/form-data" in schema["paths"]["/api/admin/variants/{variant_id}/images"]["post"]["requestBody"]["content"]
    assert backend.client.get("/docs").status_code == 200
    assert backend.client.get("/api/admin/tags", headers={**backend.headers, "Origin": "http://localhost"}).status_code == 200


@pytest.mark.parametrize("value,expected", [(" Texto local ", "Texto local"), (None, None), (" \n ", None), ("x" * 2000, "x" * 2000)])
def test_description_read_update_clear_and_public_menu(backend, value, expected):
    path = f"/api/admin/products/{backend.product_id}/description"
    assert backend.client.get(path, headers=backend.headers).status_code == 200
    response = backend.client.patch(path, headers=backend.headers, json={"description": value})
    assert response.status_code == 200 and response.json()["description"] == expected
    assert backend.client.get(path, headers=backend.headers).json()["description"] == expected
    detail = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
    assert detail["description"] == expected
    listed = backend.client.get("/api/menu").json()["products"]
    assert next(product for product in listed if product["product_id"] == backend.product_id)["description"] == expected


@pytest.mark.parametrize("payload", [
    {}, {"description": 123}, {"description": "x" * 2001},
    {"description": "<script>alert(1)</script>"}, {"description": "bad\x00text"},
    {"description": "Texto", "price": "1.00"},
])
def test_description_rejects_invalid_content_and_commercial_fields(backend, payload):
    response = backend.client.patch(f"/api/admin/products/{backend.product_id}/description", headers=backend.headers, json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("identifier,status", [(0, 422), (-1, 422), (2147483648, 422), (2147483647, 404), ("abc", 422)])
def test_identifiers_are_checked(backend, identifier, status):
    assert backend.client.get(f"/api/admin/products/{identifier}/description", headers=backend.headers).status_code == status


def test_tags_are_normalized_unique_and_associations_are_idempotent(backend):
    response = backend.client.post("/api/admin/tags", headers=backend.headers, json={"name": "  SIN\tGLUTEN "})
    assert response.status_code == 201
    tag = response.json()
    assert tag["name"] == "sin gluten" and tag["created_at"]
    duplicate = backend.client.post("/api/admin/tags", headers=backend.headers, json={"name": "Sin Gluten"})
    assert duplicate.status_code == 409
    path = f"/api/admin/variants/{backend.variant_id}/tags/{tag['tag_id']}"
    assert backend.client.put(path, headers=backend.headers).status_code == 204
    assert backend.client.put(path, headers=backend.headers).status_code == 204
    assert backend.db.connection.scalar(select(func.count()).select_from(ProductTag)) == 1
    listed = backend.client.get(f"/api/admin/variants/{backend.variant_id}/tags", headers=backend.headers).json()
    assert listed["tags"] == [tag]
    assert backend.client.get("/api/admin/tags", headers=backend.headers).json()["tags"] == [tag]
    detail = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
    variants = {variant["product_variant_id"]: variant for variant in detail["variants"]}
    assert variants[backend.variant_id]["tags"] == [{"tag_id": tag["tag_id"], "name": "sin gluten"}]
    assert variants[backend.other_id]["tags"] == []
    listed = backend.client.get("/api/menu").json()["products"]
    public_variant = next(variant for product in listed for variant in product["variants"]
                          if variant["product_variant_id"] == backend.variant_id)
    assert public_variant["tags"] == variants[backend.variant_id]["tags"]
    assert backend.client.delete(path, headers=backend.headers).status_code == 204
    assert backend.client.delete(path, headers=backend.headers).status_code == 204
    assert backend.db.connection.scalar(select(func.count()).select_from(Tag)) == 1


@pytest.mark.parametrize("name", ["", "  ", "x" * 151, "<b>Picante</b>", "bad\x00text", 123, None])
def test_invalid_tag_names(backend, name):
    assert backend.client.post("/api/admin/tags", headers=backend.headers, json={"name": name}).status_code == 422


@pytest.mark.parametrize("method,path", [
    ("GET", "/variants/2147483647/images"), ("GET", "/variants/2147483647/tags"),
    ("PUT", "/variants/2147483647/tags/2147483647"),
    ("DELETE", "/variants/2147483647/tags/2147483647"),
])
def test_missing_resources(backend, method, path):
    assert backend.client.request(method, "/api/admin" + path, headers=backend.headers).status_code == 404


@pytest.mark.parametrize("format_name,extension,mime", [("JPEG", "jpg", "image/jpeg"), ("PNG", "png", "image/png"), ("WEBP", "webp", "image/webp")])
def test_image_content_controls_format_and_is_publicly_available(backend, format_name, extension, mime):
    response = upload(backend, picture(format_name), filename="../../untrusted.exe", mime="application/octet-stream")
    assert response.status_code == 201
    image = response.json()
    assert image["image_url"].endswith("." + extension)
    assert "untrusted" not in image["image_url"] and str(backend.storage.root) not in str(image)
    assert image["product_variant_id"] == backend.variant_id and image["display_order"] == 0
    assert image_rows(backend) == 1 and not pending_files(backend)
    public = backend.client.get(image["image_url"])
    assert public.status_code == 200 and public.headers["content-type"] == mime
    assert public.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(public.content)) as decoded:
        assert decoded.format == format_name
    assert backend.client.get(f"/api/admin/variants/{backend.variant_id}/images", headers=backend.headers).json()["images"] == [image]


def test_jpeg_metadata_is_removed(backend):
    image = upload(backend, picture("JPEG", metadata=True)).json()
    with Image.open(BytesIO(backend.client.get(image["image_url"]).content)) as decoded:
        assert decoded.getexif().get(315) is None


@pytest.mark.parametrize("content,status", [
    (b"", 422), (b"not an image", 415), (b"\x89PNG\r\n\x1a\ninvalid", 422),
    (picture("GIF"), 415), (picture("PNG", animated=True), 422),
    (b"a" * (MAX_IMAGE_BYTES + 1), 413),
    (b"\xff\xd8invalid-jpeg", 422), (b"RIFF\x00\x00\x00\x00WEBPinvalid", 422),
    (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', 415),
], ids=["empty", "not-image", "corrupt-png", "gif", "animated-png", "over-5mb",
        "corrupt-jpeg", "corrupt-webp", "svg"])
def test_invalid_images_never_create_rows_or_files(backend, content, status):
    response = upload(backend, content)
    assert response.status_code == status
    assert image_rows(backend) == 0
    assert not backend.storage.root.exists()


def test_pixel_limit_is_checked_before_decompression(backend):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    header = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 5001, 4000, 8, 2, 0, 0, 0))
    header += chunk(b"IDAT", b"") + chunk(b"IEND", b"")
    assert upload(backend, header).status_code == 422
    assert image_rows(backend) == 0


def test_exactly_five_million_bytes_are_accepted_without_counting_multipart_overhead(backend):
    original = picture()
    # El relleno tras IEND no se conserva: Pillow valida y recodifica el PNG real.
    content = original + b"\x00" * (MAX_IMAGE_BYTES - len(original))
    assert len(content) == 5_000_000
    response = upload(backend, content)
    assert response.status_code == 201
    public = backend.client.get(response.json()["image_url"])
    assert public.status_code == 200 and len(public.content) < MAX_IMAGE_BYTES


def test_exactly_twenty_megapixels_are_accepted(backend):
    output = BytesIO()
    with Image.new("L", (5000, 4000), 255) as image:
        image.save(output, format="PNG")
    response = upload(backend, output.getvalue())
    assert response.status_code == 201
    with Image.open(BytesIO(backend.client.get(response.json()["image_url"]).content)) as decoded:
        assert decoded.width * decoded.height == 20_000_000


def test_image_order_integer_boundary_is_handled_without_a_database_error(backend):
    image = upload(backend, order=2_147_483_647).json()
    response = upload(backend)
    assert response.status_code == 409 and image_rows(backend) == 1
    path = f"/api/admin/variants/{backend.variant_id}/images/{image['product_image_id']}"
    assert backend.client.patch(path, headers=backend.headers, json={"display_order": 2_147_483_648}).status_code == 422


def test_request_limit_is_enforced_even_without_content_length(backend):
    chunks = (b"x" * 1_000_000 for _ in range(6))
    response = backend.client.post(f"/api/admin/variants/{backend.variant_id}/images", headers=backend.headers, content=chunks)
    assert response.status_code == 413 and image_rows(backend) == 0


def test_image_order_ownership_gallery_and_delete(backend):
    first = upload(backend, order=5).json()
    second = upload(backend, order=1).json()
    third = upload(backend).json()
    assert third["display_order"] == 6
    assert backend.client.patch(f"/api/admin/variants/{backend.other_id}/images/{first['product_image_id']}", headers=backend.headers, json={"display_order": 0}).status_code == 404
    assert backend.client.delete(f"/api/admin/variants/{backend.other_id}/images/{first['product_image_id']}", headers=backend.headers).status_code == 404
    path = f"/api/admin/variants/{backend.variant_id}/images/{first['product_image_id']}"
    assert backend.client.patch(path, headers=backend.headers, json={"display_order": -1}).status_code == 422
    assert backend.client.patch(path, headers=backend.headers, json={"display_order": 0}).status_code == 200
    listed = backend.client.get("/api/menu").json()["products"]
    product = next(product for product in listed if product["product_id"] == backend.product_id)
    assert product["image_url"] == first["image_url"]
    detail = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
    variant = next(variant for variant in detail["variants"] if variant["product_variant_id"] == backend.variant_id)
    assert [image["product_image_id"] for image in variant["images"]] == [first["product_image_id"], second["product_image_id"], third["product_image_id"]]
    assert backend.client.delete(path, headers=backend.headers).status_code == 204
    assert not backend.storage.path_for(first["image_url"]).exists()
    assert backend.client.get(first["image_url"]).status_code == 404
    assert image_rows(backend) == 2


def test_unregistered_private_inactive_and_foreign_images_are_not_public(backend):
    image = upload(backend).json()
    operation = backend.storage.prepare_create(picture(), "png", backend.product_id, backend.variant_id)
    assert backend.client.get(operation.image_url).status_code == 404
    assert backend.client.get("/media/.pending/" + operation.operation_id + ".json").status_code == 404
    assert backend.client.get(image["image_url"].replace(f"/variants/{backend.variant_id}/", f"/variants/{backend.other_id}/")).status_code == 404
    backend.db.connection.execute(update(Product).where(Product.product_id == backend.product_id).values(is_active=False))
    assert backend.client.get(image["image_url"]).status_code == 404


@pytest.mark.parametrize("url", [
    "/etc/passwd", "C:/Windows/system.ini", "/media/products/1/variants/2/../../secret",
    "/media/products/1/variants/2/" + "a" * 32 + ".svg",
])
def test_storage_rejects_uncontrolled_paths(tmp_path, url):
    with pytest.raises(StorageError):
        LocalImageStorage(tmp_path).path_for(url)


def test_missing_file_returns_a_sanitized_404(backend):
    image = upload(backend).json()
    backend.storage.path_for(image["image_url"]).unlink()
    response = backend.client.get(image["image_url"])
    assert response.status_code == 404
    assert str(backend.storage.root) not in response.text


def test_representative_image_ties_are_resolved_by_variant_then_image_id(backend):
    other = upload(backend, variant_id=backend.other_id, order=0).json()
    first = upload(backend, order=0).json()
    second = upload(backend, order=0).json()
    product = backend.client.get(f"/api/menu/{backend.product_id}").json()["product"]
    assert product["image_url"] == first["image_url"]
    variant = next(variant for variant in product["variants"] if variant["product_variant_id"] == backend.variant_id)
    assert [image["product_image_id"] for image in variant["images"]] == [first["product_image_id"], second["product_image_id"]]


def install_commit_failure(backend, monkeypatch, *, after_commit=False):
    def failing_db():
        with backend.db.sessions() as session:
            original = session.commit
            def fail():
                if after_commit:
                    original()
                raise OperationalError("PRIVATE SQL", {}, Exception("PRIVATE PASSWORD"))
            monkeypatch.setattr(session, "commit", fail)
            yield session
    backend.app.dependency_overrides[get_db] = failing_db


def test_failed_upload_commit_removes_file_and_reference(backend, monkeypatch, caplog):
    install_commit_failure(backend, monkeypatch)
    response = upload(backend)
    assert response.status_code == 503
    assert image_rows(backend) == 0 and not pending_files(backend)
    assert not list(backend.storage.root.rglob("*.png"))
    assert "PRIVATE" not in response.text + caplog.text


def test_uncertain_commit_preserves_a_committed_image(backend, monkeypatch):
    install_commit_failure(backend, monkeypatch, after_commit=True)
    response = upload(backend)
    assert response.status_code == 503 and image_rows(backend) == 1
    image = backend.db.connection.execute(select(ProductImage.__table__)).mappings().one()
    assert backend.storage.path_for(image["image_url"]).is_file()


def test_failed_delete_commit_restores_the_file(backend, monkeypatch):
    image = upload(backend).json()
    install_commit_failure(backend, monkeypatch)
    response = backend.client.delete(f"/api/admin/variants/{backend.variant_id}/images/{image['product_image_id']}", headers=backend.headers)
    assert response.status_code == 503 and image_rows(backend) == 1
    assert backend.storage.path_for(image["image_url"]).is_file()
    assert not pending_files(backend)


@pytest.mark.parametrize("failed_directory", [".pending", ".staging"], ids=["journal", "image"])
def test_failed_storage_write_leaves_no_orphan(backend, monkeypatch, failed_directory):
    original = backend.storage._write_file
    def fail(path, content):
        original(path, content)
        if path.parent.name == failed_directory:
            raise OSError("PRIVATE filesystem path")
    monkeypatch.setattr(backend.storage, "_write_file", fail)
    response = upload(backend)
    assert response.status_code == 503 and image_rows(backend) == 0
    assert not pending_files(backend) and not list(backend.storage.root.rglob("*.png"))


def test_failed_publication_move_removes_staging_and_journal(backend, monkeypatch):
    import os
    original = os.replace
    def fail(source, target):
        if source.parent.name == ".staging":
            raise OSError("temporary publication failure")
        return original(source, target)
    monkeypatch.setattr("app.services.image_storage.os.replace", fail)
    assert upload(backend).status_code == 503
    assert image_rows(backend) == 0 and not pending_files(backend)
    assert not list(backend.storage.root.rglob("*.png"))


def test_failed_upload_cleanup_remains_private_and_is_retried(backend, monkeypatch):
    original_dependency = backend.app.dependency_overrides[get_db]
    original_unlink = Path.unlink
    def fail(path, *args, **kwargs):
        if path.parent.name == str(backend.variant_id) and path.exists():
            raise OSError("temporary cleanup failure")
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        install_commit_failure(backend, patch)
        patch.setattr(Path, "unlink", fail)
        response = upload(backend)
    backend.app.dependency_overrides[get_db] = original_dependency
    assert response.status_code == 503 and image_rows(backend) == 0
    operation = json.loads(pending_files(backend)[0].read_text(encoding="utf-8"))
    assert backend.storage.path_for(operation["image_url"]).is_file()
    assert backend.client.get(operation["image_url"]).status_code == 404
    assert upload(backend).status_code == 201
    assert not pending_files(backend)
    assert not backend.storage.path_for(operation["image_url"]).exists()


def test_authenticated_database_failure_does_not_expose_the_key(backend, monkeypatch, caplog):
    install_commit_failure(backend, monkeypatch)
    response = backend.client.post("/api/admin/tags", headers=backend.headers, json={"name": "prueba segura"})
    assert response.status_code == 503
    assert backend.key not in response.text + caplog.text
    assert "PRIVATE" not in response.text + caplog.text


def test_failed_delete_move_keeps_the_record_and_file(backend, monkeypatch):
    import os
    image = upload(backend).json()
    path = backend.storage.path_for(image["image_url"])
    original = os.replace
    def fail(source, target):
        if source == path:
            raise OSError("PRIVATE path")
        return original(source, target)
    monkeypatch.setattr("app.services.image_storage.os.replace", fail)
    response = backend.client.delete(f"/api/admin/variants/{backend.variant_id}/images/{image['product_image_id']}", headers=backend.headers)
    assert response.status_code == 503 and image_rows(backend) == 1
    assert path.exists() and not pending_files(backend)


def test_cleanup_failure_stays_tracked_and_is_retried(backend, monkeypatch):
    image = upload(backend).json()
    original = Path.unlink
    def fail(path, *args, **kwargs):
        if path.parent.name == ".trash" and path.exists():
            raise OSError("temporary failure")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        response = backend.client.delete(f"/api/admin/variants/{backend.variant_id}/images/{image['product_image_id']}", headers=backend.headers)
    assert response.status_code == 204 and image_rows(backend) == 0
    assert pending_files(backend)
    assert backend.client.get(image["image_url"]).status_code == 404
    assert upload(backend).status_code == 201
    assert not pending_files(backend) and not list(backend.storage.root.glob(".trash/*"))


def test_pending_creation_and_deletion_are_recovered(backend):
    abandoned = backend.storage.prepare_create(picture(), "png", backend.product_id, backend.variant_id)
    image = upload(backend).json()
    assert not backend.storage.path_for(abandoned.image_url).exists()
    backend.storage.prepare_delete(image["image_url"])
    assert not backend.storage.path_for(image["image_url"]).exists()
    assert upload(backend).status_code == 201
    assert backend.storage.path_for(image["image_url"]).is_file()
    assert not pending_files(backend)


def test_late_creation_cleanup_cannot_remove_another_operations_journal(backend):
    content = picture()
    created = backend.storage.prepare_create(content, "png", backend.product_id, backend.variant_id)
    with backend.db.sessions() as session, session.begin():
        session.add(ProductImage(product_variant_id=backend.variant_id, image_url=created.image_url, display_order=0))
    # Otra petición recupera el diario de creación y prepara su eliminación.
    with backend.db.sessions() as session:
        service._storage_lock(session)
        backend.storage.recover(session)
        deleted = backend.storage.prepare_delete(created.image_url)
    with pytest.raises(StorageError):
        backend.storage.settle(created, True)
    assert backend.storage._paths(deleted)[3].exists()
    assert upload(backend).status_code == 201
    assert backend.storage.path_for(created.image_url).is_file()


def test_simultaneous_image_operation_returns_conflict_before_writing(backend):
    with backend.db.engine.begin() as other:
        assert other.scalar(select(func.pg_try_advisory_xact_lock(service.IMAGE_STORAGE_LOCK_KEY)))
        response = upload(backend)
    assert response.status_code == 409 and image_rows(backend) == 0
    assert not backend.storage.root.exists()


def test_local_enrichment_survives_sync_without_changing_commercial_fields(backend, commercial_snapshot):
    description_path = f"/api/admin/products/{backend.product_id}/description"
    assert backend.client.patch(description_path, headers=backend.headers, json={"description": "Local"}).status_code == 200
    image = upload(backend).json()
    tag = backend.client.post("/api/admin/tags", headers=backend.headers, json={"name": "PICANTE"}).json()
    backend.client.put(f"/api/admin/variants/{backend.variant_id}/tags/{tag['tag_id']}", headers=backend.headers)
    before = backend.client.get(f"/api/menu/{backend.product_id}").json()
    backend.db.service.persist_snapshot(commercial_snapshot)
    assert backend.client.get(f"/api/menu/{backend.product_id}").json() == before
    assert backend.client.get(image["image_url"]).status_code == 200


def test_commercial_retirement_hides_images_without_deleting_local_enrichment(backend, commercial_snapshot):
    image = upload(backend).json()
    tag = backend.client.post("/api/admin/tags", headers=backend.headers, json={"name": "persistente"}).json()
    path = f"/api/admin/variants/{backend.variant_id}/tags/{tag['tag_id']}"
    assert backend.client.put(path, headers=backend.headers).status_code == 204
    snapshot = deepcopy(commercial_snapshot)
    snapshot.items[0].clear()
    snapshot.items[0].update(id="burger", deleted_at="2026-11-02T18:00:00Z")
    backend.db.service.persist_snapshot(snapshot)
    assert backend.client.get(f"/api/menu/{backend.product_id}").status_code == 404
    assert backend.client.get(image["image_url"]).status_code == 404
    assert backend.storage.path_for(image["image_url"]).is_file()
    assert image_rows(backend) == 1
    assert backend.db.connection.scalar(select(func.count()).select_from(ProductTag)) == 1
    assert backend.db.connection.scalar(select(func.count()).select_from(Tag)) == 1


@pytest.mark.parametrize("count,menu_queries,detail_queries", [(1, 4, 6), (25, 4, 6), (501, 6, 9)])
def test_enriched_menu_queries_are_batched(backend, count, menu_queries, detail_queries):
    image = upload(backend).json()
    with backend.db.sessions() as session, session.begin():
        for index in range(count):
            variant = ProductVariant(
                product_id=backend.product_id, loyverse_id=f"batch-local-{index}",
                name="Variante", price=backend.variants["single"].price,
                synced_at=backend.variants["single"].synced_at,
            )
            session.add(variant)
            session.flush()
            tag = Tag(name=f"etiqueta {index}")
            session.add(ProductTag(variant=variant, tag=tag))
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(backend.db.connection, "before_cursor_execute", record)
    try:
        assert backend.client.get("/api/menu").status_code == 200
        assert len(statements) == menu_queries
        statements.clear()
        assert backend.client.get(f"/api/menu/{backend.product_id}").status_code == 200
        assert len(statements) == detail_queries
    finally:
        event.remove(backend.db.connection, "before_cursor_execute", record)

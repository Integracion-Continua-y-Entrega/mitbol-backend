"""Contrato R2/S3 y fallos sin credenciales ni conexiones de proveedor reales."""

from io import BytesIO
import json
from types import SimpleNamespace

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from botocore.stub import Stubber
from pydantic import ValidationError
import pytest

from app.core.config import Settings
from app.main import create_app
from app.services.enrichment_errors import StorageError
from app.services.image_storage import FileOperation, LocalImageStorage, create_image_storage
from app.services.r2_image_storage import R2ImageStorage


ENDPOINT = "https://" + "a" * 32 + ".r2.cloudflarestorage.com"
URL = "/media/products/1/variants/2/" + "c" * 32 + ".png"


def settings(**values):
    config = dict(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
                  image_storage_provider="r2", r2_endpoint_url=ENDPOINT, r2_bucket="synthetic-images",
                  r2_access_key_id="synthetic-access-key", r2_secret_access_key="synthetic-private-secret")
    return Settings(**{**config, **values})


class FakeS3:
    """Objetos compartidos en memoria; reabrir el adaptador simula un reinicio."""
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail = None
        self.closed = False

    def _call(self, method, kwargs):
        self.calls.append((method, kwargs))
        if self.fail:
            self.fail(method, kwargs)

    @staticmethod
    def error(code="AccessDenied", status=403):
        return ClientError({"Error": {"Code": code, "Message": "PRIVATE synthetic-private-secret"},
                            "ResponseMetadata": {"HTTPStatusCode": status}}, "SyntheticOperation")

    def put_object(self, **kwargs):
        self._call("put", kwargs)
        key = kwargs["Key"]
        assert kwargs["IfNoneMatch"] == "*" and "ACL" not in kwargs
        if key in self.objects:
            raise self.error("PreconditionFailed", 412)
        self.objects[key] = (bytes(kwargs["Body"]), dict(kwargs["Metadata"]))
        return {}

    def get_object(self, **kwargs):
        self._call("get", kwargs)
        if kwargs["Key"] not in self.objects:
            raise self.error("NoSuchKey", 404)
        data, metadata = self.objects[kwargs["Key"]]
        return {"Body": BytesIO(data), "ContentLength": len(data), "Metadata": metadata}

    def head_object(self, **kwargs):
        self._call("head", kwargs)
        if kwargs["Key"] not in self.objects:
            raise self.error("404", 404)
        data, metadata = self.objects[kwargs["Key"]]
        return {"ContentLength": len(data), "Metadata": metadata}

    def delete_object(self, **kwargs):
        self._call("delete", kwargs)
        self.objects.pop(kwargs["Key"], None)
        return {}

    def list_objects_v2(self, **kwargs):
        self._call("list", kwargs)
        return {"IsTruncated": False, "Contents": [{"Key": key} for key in sorted(self.objects)
                                                   if key.startswith(kwargs["Prefix"])]}

    def close(self):
        self.closed = True


def r2(client=None):
    return R2ImageStorage(bucket="synthetic-images", prefix="test/images", client=client or FakeS3())


@pytest.mark.parametrize("provider", ["local", "r2"])
def test_common_contract_roundtrip_and_delete_rollback(tmp_path, provider):
    storage = LocalImageStorage(tmp_path / "images") if provider == "local" else r2()
    operation = storage.prepare_create(b"processed-image", "png", 1, 2)
    storage.settle(operation, True)
    stored = storage.open_image(operation.image_url)
    with stored.stream as stream:
        assert stream.read() == b"processed-image" and stored.size == 15
    delete = storage.prepare_delete(operation.image_url)
    storage.settle(delete, True)  # PostgreSQL conservó la referencia.
    with storage.open_image(operation.image_url).stream as stream:
        assert stream.read() == b"processed-image"
    delete = storage.prepare_delete(operation.image_url)
    storage.settle(delete, False)
    with pytest.raises(FileNotFoundError):
        storage.open_image(operation.image_url)


@pytest.mark.parametrize("field,value", [
    ("image_storage_provider", "s3-public"), ("r2_endpoint_url", "http://localhost:9000"),
    ("r2_endpoint_url", ENDPOINT + "/arbitrary"), ("r2_endpoint_url", "https://secret@evil.example"),
    ("r2_bucket", "../bucket"), ("r2_bucket", "UPPERCASE"),
    ("r2_access_key_id", None), ("r2_secret_access_key", "secret with spaces"),
    ("r2_key_prefix", "../images"), ("r2_key_prefix", "/images"),
])
def test_invalid_configuration_fails_without_exposing_secrets(field, value):
    with pytest.raises(ValidationError) as error:
        settings(**{field: value})
    assert "synthetic-private-secret" not in str(error.value)


def test_local_default_does_not_require_r2_and_factory_revalidates_copied_settings(tmp_path):
    config = settings(image_storage_provider="local", r2_secret_access_key=None, image_storage_root=tmp_path)
    assert isinstance(create_image_storage(config), LocalImageStorage)
    config = config.model_copy(update={"image_storage_provider": "r2"})
    with pytest.raises(ValueError):
        create_app(config)


def test_factory_is_lazy_and_boto_configuration_is_explicit(monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://must-not-be-used.example")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "must-not-be-used")
    storage = create_image_storage(settings())
    assert storage._client is None
    client = storage._client_factory()
    try:
        assert client.meta.endpoint_url == ENDPOINT and client.meta.region_name == "auto"
        config = client.meta.config
        assert config.signature_version == "s3v4" and config.s3["addressing_style"] == "path"
        assert config.retries["total_max_attempts"] == 1
        assert config.request_checksum_calculation == "when_required"
        assert config.response_checksum_validation == "when_required"
        assert config.proxies == {}
        assert client._request_signer._credentials.access_key == "synthetic-access-key"
    finally:
        client.close()


def test_real_sdk_models_accept_put_read_delete_requests_without_network():
    client = boto3.client("s3", endpoint_url=ENDPOINT, region_name="auto",
                          aws_access_key_id="synthetic", aws_secret_access_key="synthetic")
    storage = r2(client)
    key = storage.key_for(URL)
    with Stubber(client) as stub:
        stub.add_response("put_object", {}, {"Bucket": storage.bucket, "Key": key,
            "Body": b"image", "ContentType": "image/png", "Metadata": {}, "IfNoneMatch": "*"})
        stub.add_response("get_object", {"Body": BytesIO(b"image"), "ContentLength": 5},
                          {"Bucket": storage.bucket, "Key": key})
        stub.add_response("delete_object", {}, {"Bucket": storage.bucket, "Key": key})
        storage._put(key, b"image", "image/png")
        with storage.open_image(URL).stream as stream:
            assert stream.read() == b"image"
        storage._delete(key)
        stub.assert_no_pending_responses()
    storage.close()


@pytest.mark.parametrize("error", [FakeS3.error(), FakeS3.error("NoSuchBucket", 404),
    EndpointConnectionError(endpoint_url="PRIVATE endpoint"), ReadTimeoutError(endpoint_url="PRIVATE endpoint")])
def test_provider_errors_are_sanitized_and_not_confused_with_missing(error):
    client = FakeS3()
    client.fail = lambda *args: (_ for _ in ()).throw(error)
    with pytest.raises(StorageError) as caught:
        r2(client).open_image(URL)
    assert caught.value.status_code == 503 and "PRIVATE" not in str(caught.value)


def test_restart_recovers_referenced_create_and_unreferenced_objects():
    client = FakeS3()
    storage = r2(client)
    keep = storage.prepare_create(b"keep", "png", 1, 2)
    discard = storage.prepare_create(b"discard", "png", 1, 2)
    # La fila quedó confirmada, pero el objeto final falta: recuperar staging.
    client.objects.pop(storage.key_for(keep.image_url))
    restarted = r2(client)
    db = SimpleNamespace(scalar=lambda statement: 1 if keep.image_url in statement.compile().params.values() else None)
    restarted.recover(db)
    with restarted.open_image(keep.image_url).stream as stream:
        assert stream.read() == b"keep"
    assert storage.key_for(discard.image_url) not in client.objects
    assert list(client.objects) == [storage.key_for(keep.image_url)]


def test_ambiguous_put_retains_durable_intent_after_negative_head():
    client = FakeS3()
    storage = r2(client)
    delayed = []

    def fail(method, kwargs):
        if method == "put" and "/variants/" in kwargs["Key"]:
            delayed.append(dict(kwargs))
            raise ReadTimeoutError(endpoint_url="PRIVATE")
    client.fail = fail
    with pytest.raises(StorageError):
        storage.prepare_create(b"late", "png", 1, 2)
    client.fail = None
    restarted = r2(client)
    no_reference = SimpleNamespace(scalar=lambda statement: None)
    restarted.recover(no_reference)
    assert any("/.pending/" in key for key in client.objects)
    client.put_object(**delayed[0])  # La solicitud original termina después.
    restarted.recover(no_reference)
    assert not client.objects


def test_collision_never_overwrites_or_deletes_an_existing_object(monkeypatch):
    storage = r2()
    monkeypatch.setattr("app.services.r2_image_storage.uuid4", lambda: SimpleNamespace(hex="c" * 32))
    key = storage.key_for(URL)
    storage._client.objects[key] = (b"existing", {})
    with pytest.raises(StorageError):
        storage.prepare_create(b"new", "png", 1, 2)
    with pytest.raises(StorageError):
        storage.recover(SimpleNamespace(scalar=lambda _: None))
    assert storage._client.objects[key] == (b"existing", {})


@pytest.mark.parametrize("payload", [b"not json", b"[]", b"{}", json.dumps({
    "kind": "delete", "image_url": "../../foreign", "operation_id": "b" * 32, "sha256": None}).encode()])
def test_invalid_journal_fails_closed_without_deleting_objects(payload):
    storage = r2()
    key = storage.prefix + "/.pending/" + "b" * 32 + ".json"
    storage._client.objects[key] = (payload, {})
    with pytest.raises(StorageError):
        storage.recover(SimpleNamespace(scalar=lambda _: None))
    assert key in storage._client.objects and not any(method == "delete" for method, _ in storage._client.calls)


def test_recovery_paginates_and_rejects_nonadvancing_tokens(monkeypatch):
    storage = r2()
    pages = iter([{"IsTruncated": True, "NextContinuationToken": "next"}, {"IsTruncated": False}])
    monkeypatch.setattr(storage._client, "list_objects_v2", lambda **kwargs: next(pages))
    storage.recover(SimpleNamespace(scalar=lambda _: None))
    monkeypatch.setattr(storage._client, "list_objects_v2", lambda **kwargs: {"IsTruncated": True, "NextContinuationToken": "same"})
    with pytest.raises(StorageError):
        storage.recover(SimpleNamespace(scalar=lambda _: None))


def test_remote_body_is_closed_and_size_checked_before_streaming(monkeypatch):
    storage = r2()
    body = BytesIO(b"short")
    monkeypatch.setattr(storage._client, "get_object", lambda **kwargs: {"Body": body, "ContentLength": 100})
    with pytest.raises(StorageError):
        storage.open_image(URL)
    assert body.closed


def test_cloud_restaurant_image_write_restriction_is_unchanged(monkeypatch):
    from test_auth_cloud import render_config
    config = render_config(monkeypatch)
    config = config.model_copy(update={"auth_images_enabled": True})
    with pytest.raises(ValueError, match="escrituras de imágenes"):
        config.validate_auth_configuration()

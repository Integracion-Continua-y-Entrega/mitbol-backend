"""R2 privado mediante S3; intenciones durables, sin rutas públicas del bucket.

Cada prefijo pertenece a UNA base de datos. recover requiere el bloqueo de
imágenes de esa base. No hay transacción distribuida: el diario permite resolver
fallos y commits inciertos sin decidir a partir del resultado HTTP anterior.
"""

from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
import re
from threading import Lock
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from app.models import ProductImage
from app.services.enrichment_errors import StorageError
from app.services.image_storage import FileOperation, ImageFile, MAX_IMAGE_BYTES, image_parts


class R2ImageStorage:
    def __init__(self, *, bucket: str, prefix: str, client=None, client_factory=None, endpoint=""):
        self.bucket, self.prefix = bucket, prefix
        self._client, self._client_factory = client, client_factory
        self._endpoint = endpoint
        self._client_lock = Lock()

    @classmethod
    def from_settings(cls, config):
        config.validate_image_storage_configuration()

        def client_factory():
            import boto3
            from botocore.config import Config

            # Credenciales explícitas: sin perfiles, IMDS ni credenciales del host.
            session = boto3.session.Session(
                aws_access_key_id=config.r2_access_key_id.get_secret_value(),
                aws_secret_access_key=config.r2_secret_access_key.get_secret_value(),
                region_name="auto",
            )
            return session.client("s3", endpoint_url=config.r2_endpoint_url, config=Config(
                signature_version="s3v4", s3={"addressing_style": "path"},
                connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                ignore_configured_endpoint_urls=True, proxies={},
            ))

        return cls(bucket=config.r2_bucket, prefix=config.r2_key_prefix,
                   endpoint=config.r2_endpoint_url, client_factory=client_factory)

    @property
    def identity(self) -> tuple:
        return ("r2", self._endpoint, self.bucket, self.prefix)

    def close(self):
        if self._client is not None:
            self._client.close()

    def _call(self, method, **kwargs):
        try:
            with self._client_lock:
                if self._client is None:
                    self._client = self._client_factory()
            return getattr(self._client, method)(Bucket=self.bucket, **kwargs)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if method in {"get_object", "head_object"} and code in {"NoSuchKey", "NotFound", "404"}:
                raise FileNotFoundError() from None
            raise StorageError() from None
        except (BotoCoreError, OSError):
            raise StorageError() from None

    def key_for(self, image_url: str) -> str:
        product, variant, filename, _ = image_parts(image_url)
        return f"{self.prefix}/{product}/variants/{variant}/{filename}"

    def _paths(self, operation):
        if operation.kind not in {"create", "delete"} or not re.fullmatch(r"[0-9a-f]{32}", operation.operation_id):
            raise StorageError()
        final = self.key_for(operation.image_url)
        extension = final.rsplit(".", 1)[1]
        return (final, f"{self.prefix}/.staging/{operation.operation_id}.{extension}",
                f"{self.prefix}/.pending/{operation.operation_id}.json")

    def _read(self, key, *, limit):
        response = self._call("get_object", Key=key)
        body = response["Body"]
        try:
            size = response["ContentLength"]
            if not isinstance(size, int) or not 0 <= size <= limit:
                raise StorageError()
            data = body.read(limit + 1)
            if len(data) != size:
                raise StorageError()
            return data
        except (BotoCoreError, OSError):
            raise StorageError() from None
        finally:
            body.close()

    def open_image(self, image_url: str) -> ImageFile:
        # <=5 MB: completar la lectura antes de enviar headers evita publicar un
        # 200 truncado o filtrar excepciones del SDK desde un generador HTTP.
        data = self._read(self.key_for(image_url), limit=MAX_IMAGE_BYTES)
        return ImageFile(BytesIO(data), len(data))

    def _head(self, key):
        try:
            return self._call("head_object", Key=key)
        except FileNotFoundError:
            return None

    def _put(self, key, data, content_type, *, metadata=None):
        return self._call("put_object", Key=key, Body=data, ContentType=content_type,
                          Metadata=metadata or {}, IfNoneMatch="*")

    def _delete(self, key):
        self._call("delete_object", Key=key)

    def _record(self, operation, digest=None):
        payload = {**asdict(operation), "sha256": digest}
        self._put(self._paths(operation)[2], json.dumps(payload).encode(), "application/json")

    def _journal(self, key):
        try:
            payload = json.loads(self._read(key, limit=4096))
            digest = payload.pop("sha256")
            operation = FileOperation(**payload)
            if self._paths(operation)[2] != key:
                raise StorageError()
            if operation.kind == "create" and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise StorageError()
            return operation, digest
        except FileNotFoundError:
            return None
        except (TypeError, ValueError, KeyError, AttributeError):
            raise StorageError() from None

    @staticmethod
    def _metadata(operation, digest):
        return {"mitbol-operation": operation.operation_id, "sha256": digest}

    def prepare_create(self, content: bytes, extension: str, product_id: int, variant_id: int) -> FileOperation:
        if extension not in {"jpg", "png", "webp"} or not 0 < len(content) <= MAX_IMAGE_BYTES:
            raise StorageError()
        operation = FileOperation("create", f"/media/products/{product_id}/variants/{variant_id}/{uuid4().hex}.{extension}")
        final, staging, _ = self._paths(operation)
        digest = sha256(content).hexdigest()
        self._record(operation, digest)
        mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[extension]
        metadata = self._metadata(operation, digest)
        self._put(staging, content, mime, metadata=metadata)
        self._put(final, content, mime, metadata=metadata)
        return operation

    def prepare_delete(self, image_url: str) -> FileOperation:
        operation = FileOperation("delete", image_url)
        self._record(operation)
        # El objeto permanece intacto hasta comprobar que PostgreSQL quitó la
        # última referencia. Rollback/auditoría fallida no requieren restaurarlo.
        return operation

    def settle(self, operation: FileOperation, referenced: bool):
        final, staging, journal = self._paths(operation)
        entry = self._journal(journal)
        if entry is None:
            return  # Otra recuperación ya resolvió esta intención.
        recorded, digest = entry
        if recorded != operation:
            raise StorageError()
        if operation.kind == "delete":
            if not referenced:
                self._delete(final)
            self._delete(journal)
            return

        head = self._head(final)
        if head is None:
            if not referenced:
                # Una escritura que agotó su timeout puede terminar DESPUÉS de
                # este HEAD negativo. Conservar intención/staging evita perder
                # su rastro; revisar/reintentar posteriormente, sin borrar datos.
                return
            content = self._read(staging, limit=MAX_IMAGE_BYTES)
            if sha256(content).hexdigest() != digest:
                raise StorageError()
            mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[final.rsplit(".", 1)[1]]
            self._put(final, content, mime, metadata=self._metadata(operation, digest))
        elif head.get("Metadata") != self._metadata(operation, digest):
            # No sobrescribir ni borrar un objeto ajeno ante una colisión.
            raise StorageError()
        if not referenced:
            self._delete(final)
        self._delete(staging)
        self._delete(journal)

    def recover(self, db):
        """Sólo bajo el advisory lock; resuelve contra referencias actuales."""
        prefix = f"{self.prefix}/.pending/"
        token = None
        while True:
            page = self._call("list_objects_v2", Prefix=prefix,
                              **({"ContinuationToken": token} if token else {}))
            for item in page.get("Contents", []):
                key = item["Key"]
                if not re.fullmatch(re.escape(prefix) + r"[0-9a-f]{32}\.json", key):
                    continue
                entry = self._journal(key)
                if entry is None:
                    continue
                operation, _ = entry
                referenced = db.scalar(select(ProductImage.product_image_id).where(
                    ProductImage.image_url == operation.image_url,
                ).limit(1)) is not None
                self.settle(operation, referenced)
            if not page.get("IsTruncated"):
                break
            next_token = page.get("NextContinuationToken")
            if not next_token or next_token == token:
                raise StorageError()
            token = next_token

"""Prueba REAL R2, opt-in, limitada a objetos creados en un prefijo único.

Lee el .env local mediante Settings; no importa FastAPI, crea motores SQL,
conecta PostgreSQL ni consulta otros servicios. No cambia configuración R2.
"""

import argparse
from hashlib import sha256
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import sys
import warnings
from uuid import uuid4

logging.disable(logging.CRITICAL)
warnings.simplefilter("ignore")
PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    report = {"configuration_valid": False, "database_contacted": False,
              "remote_configuration_changed": False, "upload": False,
              "existence_verified": False, "read": False, "sha256_matches": False,
              "delete": False, "prefix_empty_after": False,
              "full_bucket_privacy": "requires_public_domain_settings_check"}
    storage = guard = None
    stage = "configuration"
    exit_code = 1
    try:
        from dotenv import dotenv_values
        local_values = dotenv_values(PROJECT / '.env', interpolate=False)
        configured_prefix = os.environ.get('R2_KEY_PREFIX', local_values.get('R2_KEY_PREFIX', 'mitbol/product-images'))
        report['configured_prefix_accepted'] = bool(re.fullmatch(r'[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*', configured_prefix or ''))
        test_prefix = 'mitbol-r2-smoke/' + uuid4().hex
        # Prefijo independiente, válido y exclusivo de ESTA ejecución. La
        # prioridad del entorno evita cambiar el prefijo inválido del .env.
        os.environ['R2_KEY_PREFIX'] = test_prefix
        report['validation_uses_temporary_prefix'] = True
        # Prohibición adicional contra conexiones SQL accidentales, antes de
        # importar la configuración. Ningún paso de la prueba necesita una BD.
        import psycopg2
        import sqlalchemy
        def no_database(*args, **kwargs):
            raise RuntimeError("Database access prohibited")
        psycopg2.connect = no_database
        sqlalchemy.create_engine = no_database
        from app.core.config import settings
        from app.services.image_storage import create_image_storage, validate_image
        from botocore.exceptions import ClientError
        from PIL import Image

        if Path.cwd().resolve() != PROJECT or settings.app_env != "local":
            raise RuntimeError("Local project environment required")
        if settings.image_storage_provider != "r2":
            raise RuntimeError("R2 provider required")
        settings.validate_image_storage_configuration()
        report["configuration_valid"] = True
        if not args.execute:
            report["mode"] = "configuration_only"
            exit_code = 0
            return exit_code

        prefix = test_prefix
        report["temporary_prefix"] = prefix
        # Copia en memoria: el .env y el prefijo habitual no cambian.
        config = settings.model_copy(update={"r2_key_prefix": prefix})
        storage = create_image_storage(config)

        class ScopedClient:
            def __init__(self, client):
                self.client = client
                self.owned = set()
                self.put_confirmed = set()
                self.deleted = set()
                self.inflight_uncertain = set()
                self.errors = []

            def request(self, method, kwargs):
                if kwargs.get("Bucket") != config.r2_bucket:
                    raise RuntimeError("Unexpected bucket")
                key = kwargs.get("Key")
                if method == "list_objects_v2":
                    if kwargs.get("Prefix") != prefix + "/":
                        raise RuntimeError("Listing outside test prefix prohibited")
                elif not isinstance(key, str) or not key.startswith(prefix + "/"):
                    raise RuntimeError("Object outside test prefix prohibited")
                if method == "delete_object" and key not in self.owned:
                    raise RuntimeError("Deleting an object not created by this test prohibited")
                if method == "put_object":
                    if kwargs.get("IfNoneMatch") != "*":
                        raise RuntimeError("Overwriting an existing object prohibited")
                    self.owned.add(key)
                try:
                    result = getattr(self.client, method)(**kwargs)
                except Exception as error:
                    code = None
                    status = None
                    if isinstance(error, ClientError):
                        candidate = error.response.get("Error", {}).get("Code", "")
                        if candidate in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch",
                                         "NoSuchBucket", "NoSuchKey", "404", "NotFound", "PreconditionFailed",
                                         "InvalidRequest", "NotImplemented", "RequestTimeTooSkewed"}:
                            code = candidate
                        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                        if method == "put_object" and status == 412:
                            self.owned.discard(key)
                    if method == "put_object" and not isinstance(error, ClientError):
                        self.inflight_uncertain.add(key)
                    self.errors.append({"method": method, "type": type(error).__name__, "code": code,
                                        "http_status": status if isinstance(status, int) else None})
                    raise
                if method == "put_object":
                    self.put_confirmed.add(key)
                if method == "delete_object":
                    self.deleted.add(key)
                return result

            def put_object(self, **kwargs): return self.request("put_object", kwargs)
            def get_object(self, **kwargs): return self.request("get_object", kwargs)
            def head_object(self, **kwargs): return self.request("head_object", kwargs)
            def delete_object(self, **kwargs): return self.request("delete_object", kwargs)
            def list_objects_v2(self, **kwargs): return self.request("list_objects_v2", kwargs)
            def close(self): self.client.close()

        stage = "connect_and_check_empty_prefix"
        guard = ScopedClient(storage._client_factory())
        storage._client = guard
        initial = storage._call("list_objects_v2", Prefix=prefix + "/", MaxKeys=1)
        if initial.get("Contents"):
            raise RuntimeError("Test prefix is not empty")
        report["connection"] = True

        png = BytesIO()
        Image.new("RGB", (16, 16), (44, 109, 74)).save(png, format="PNG")
        content, extension = validate_image(png.getvalue())
        report["image_size_bytes"] = len(content)
        stage = "upload"
        operation = storage.prepare_create(content, extension, 1, 1)
        report["upload"] = True
        key = storage.key_for(operation.image_url)
        stage = "head"
        head = storage._head(key)
        if head is None or head.get("ContentLength") != len(content):
            raise RuntimeError("Object existence verification failed")
        report["existence_verified"] = True
        stage = "read_and_verify"
        stored = storage.open_image(operation.image_url)
        with stored.stream as stream:
            returned = stream.read()
        report["read"] = True
        report["sha256_matches"] = (stored.size == len(content) and sha256(returned).digest() == sha256(content).digest())
        if not report["sha256_matches"]:
            raise RuntimeError("Image integrity mismatch")
        stage = "settle_created_object"
        # Esta prueba no registra referencias PostgreSQL. True conserva el
        # objeto solo para probar después el protocolo de eliminación completo.
        storage.settle(operation, referenced=True)

        stage = "anonymous_access_check"
        import httpx
        from urllib.parse import quote
        url = config.r2_endpoint_url.rstrip("/") + "/" + quote(config.r2_bucket, safe="") + "/" + quote(key, safe="/")
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as http:
            anonymous = http.get(url)
        report["anonymous_s3_http_status"] = anonymous.status_code
        report["anonymous_s3_read_denied"] = anonymous.status_code in {401, 403}
        if anonymous.status_code == 200:
            report["anonymous_returned_test_image"] = sha256(anonymous.content).digest() == sha256(content).digest()

        stage = "delete"
        removal = storage.prepare_delete(operation.image_url)
        storage.settle(removal, referenced=False)
        if storage._head(key) is not None:
            raise RuntimeError("Object still exists after deletion")
        report["delete"] = True
        stage = "verify_cleanup"
        remaining = storage._call("list_objects_v2", Prefix=prefix + "/", MaxKeys=1)
        report["prefix_empty_after"] = not remaining.get("Contents")
        report["created_keys_count"] = len(guard.put_confirmed)
        report["deleted_only_test_objects"] = guard.deleted <= guard.owned
        if not report["prefix_empty_after"]:
            raise RuntimeError("Temporary objects remain")
        report["status"] = "success"
        exit_code = 0
    except Exception as error:
        # No imprimir excepciones ni tracebacks: pueden contener endpoint,
        # firmas, Authorization, nombres privados o configuración sensible.
        report.update(status="failed", failed_stage=stage, error_type=type(error).__name__)
        if guard:
            report["provider_error"] = guard.errors[-1] if guard.errors else None
    finally:
        if guard:
            cleanup_errors = 0
            for key in sorted(guard.owned - guard.deleted):
                if key in guard.inflight_uncertain:
                    continue  # No concluir ausencia mientras un PUT pueda llegar tarde.
                try:
                    guard.delete_object(Bucket=config.r2_bucket, Key=key)
                except Exception:
                    cleanup_errors += 1
            report["cleanup_errors"] = cleanup_errors
            report["inflight_uncertain_count"] = len(guard.inflight_uncertain)
            storage.close()
        print(json.dumps(report, ensure_ascii=True, sort_keys=True), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

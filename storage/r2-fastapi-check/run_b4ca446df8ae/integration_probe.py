"""Ensayo REAL opt-in: FastAPI sin mocks + PostgreSQL desechable + R2 privado.

Archivo fuera de la colección normal de pytest. Ejecutar solo explícitamente
mediante scripts/test_auth_phase1.py, que sanea el entorno y aísla PostgreSQL.
No cambia configuración, no importa URLs SQL del .env y solo usa su R2/clave
administrativa. Toda salida se limita a un informe sin secretos.
"""

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
import sys
from uuid import uuid4
import warnings

logging.disable(logging.CRITICAL)
warnings.simplefilter('ignore')
PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'tests'))

import pytest
from conftest import sync_test_database  # Mismo mecanismo PostgreSQL seguro existente.


class ProbeFailure(Exception):
    pass


def ensure(condition, label):
    if not condition:
        raise ProbeFailure(label)


def read_private_settings(database_url, prefix):
    from dotenv import dotenv_values
    from app.core.config import Settings
    values = dotenv_values(PROJECT / '.env', interpolate=False)
    required = ('R2_ENDPOINT_URL', 'R2_BUCKET', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY',
                'R2_KEY_PREFIX', 'ADMIN_API_KEY')
    ensure(all(values.get(name) for name in required), 'missing_private_configuration')
    ensure(values.get('IMAGE_STORAGE_PROVIDER') == 'r2', 'private_provider_is_not_r2')
    ensure(bool(re.fullmatch(r'[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*', values['R2_KEY_PREFIX'])),
           'configured_prefix_invalid')
    ensure(str(values.get('ADMIN_ENABLED', '')).lower() == 'true', 'local_admin_not_enabled')
    # Lista permitida: nunca pasar DATABASE_URL, AUTH_DATABASE_URL ni Loyverse
    # del archivo privado. No exportar secretos al entorno ni a comandos.
    return Settings(_env_file=None, app_env='test', database_url=database_url,
                    image_storage_provider='r2', r2_key_prefix=prefix,
                    r2_endpoint_url=values['R2_ENDPOINT_URL'], r2_bucket=values['R2_BUCKET'],
                    r2_access_key_id=values['R2_ACCESS_KEY_ID'],
                    r2_secret_access_key=values['R2_SECRET_ACCESS_KEY'],
                    admin_enabled=True, admin_api_key=values['ADMIN_API_KEY'],
                    auth_enabled=False, auth_catalog_enabled=False, auth_images_enabled=False,
                    loyverse_access_token=None, loyverse_store_id=None)


class ScopedR2Client:
    """Reenvía peticiones REALES; impide acceder fuera del prefijo del ensayo."""
    def __init__(self, client, bucket, prefix):
        self.client, self.bucket, self.prefix = client, bucket, prefix
        self.attempted = set()
        self.confirmed = set()
        self.deleted = set()
        self.uncertain = set()
        self.errors = []

    def request(self, method, kwargs):
        from botocore.exceptions import ClientError
        ensure(kwargs.get('Bucket') == self.bucket, 'unexpected_bucket')
        key = kwargs.get('Key')
        if method == 'list_objects_v2':
            ensure(kwargs.get('Prefix') in {self.prefix + '/', self.prefix + '/.pending/'},
                   'listing_outside_test_prefix')
        else:
            ensure(isinstance(key, str) and key.startswith(self.prefix + '/'), 'key_outside_test_prefix')
        if method == 'delete_object':
            ensure(key in self.attempted, 'deletion_of_unowned_object')
        if method == 'put_object':
            ensure(kwargs.get('IfNoneMatch') == '*', 'overwriting_existing_object')
            self.attempted.add(key)
        try:
            response = getattr(self.client, method)(**kwargs)
        except Exception as error:
            code, status = None, None
            if isinstance(error, ClientError):
                raw_code = error.response.get('Error', {}).get('Code')
                status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
                if raw_code in {'AccessDenied', 'InvalidAccessKeyId', 'SignatureDoesNotMatch',
                                'NoSuchBucket', 'NoSuchKey', '404', 'NotFound',
                                'PreconditionFailed', 'InvalidRequest', 'NotImplemented'}:
                    code = raw_code
                if method == 'put_object' and status == 412:
                    self.attempted.discard(key)
            elif method == 'put_object':
                self.uncertain.add(key)
            self.errors.append({'method': method, 'type': type(error).__name__,
                                'code': code, 'http_status': status if isinstance(status, int) else None})
            raise
        if method == 'put_object':
            self.confirmed.add(key)
        if method == 'delete_object':
            self.deleted.add(key)
        return response

    def put_object(self, **kwargs): return self.request('put_object', kwargs)
    def get_object(self, **kwargs): return self.request('get_object', kwargs)
    def head_object(self, **kwargs): return self.request('head_object', kwargs)
    def delete_object(self, **kwargs): return self.request('delete_object', kwargs)
    def list_objects_v2(self, **kwargs): return self.request('list_objects_v2', kwargs)
    def close(self): self.client.close()


def test_real_fastapi_postgresql_r2_roundtrip(sync_test_database, monkeypatch):
    from fastapi.testclient import TestClient
    from PIL import Image, PngImagePlugin
    import psycopg2
    from psycopg2.extensions import parse_dsn
    from sqlalchemy import func, select, text
    from app.main import create_app
    from app.models import Product, ProductImage, ProductVariant
    from app.services.image_storage import validate_image

    report = {'status': 'failed', 'database_isolated': False, 'r2_real': True,
              'fastapi_dependency_overrides': False, 'shared_services_contacted': False,
              'source_code_modified': False, 'configured_prefix_valid': False}
    prefix = 'mitbol-r2-fastapi-smoke/' + uuid4().hex
    report['temporary_prefix'] = prefix
    dotenv_hash = sha256((PROJECT / '.env').read_bytes()).digest()
    application = storage = guard = None
    stage, failure = 'validate_local_database', None
    try:
        engine = sync_test_database
        directory = Path(os.environ['MITBOL_SYNC_TEST_CLUSTER']).resolve()
        port = int(os.environ['MITBOL_SYNC_TEST_PORT'])
        ensure(engine.url.host == '127.0.0.1' and engine.url.port == port, 'unexpected_database_address')
        ensure(bool(re.fullmatch(r'mitbol_test_hu008_[a-f0-9]{12}', engine.url.database or '')),
               'database_name_not_disposable')
        with engine.connect() as db:
            identity = db.execute(text("SELECT current_database(), current_user, host(inet_server_addr()), "
                                       "inet_server_port(), current_setting('data_directory'), "
                                       "current_setting('server_version_num')")).one()
        ensure(identity[0] == engine.url.database and identity[1] == 'mitbol_ci' and identity[2] == '127.0.0.1',
               'database_identity_mismatch')
        ensure(identity[3] == port and Path(identity[4]).resolve() == directory and int(identity[5]) // 10000 == 18,
               'database_cluster_mismatch')
        report.update(database_isolated=True, postgresql_version=18, schema_source='temporary_metadata_no_migrations')

        original_connect = psycopg2.connect
        def guarded_connect(*args, **kwargs):
            params = parse_dsn(args[0]) if args and isinstance(args[0], str) else {}
            params.update(kwargs)
            ensure(params.get('host') == '127.0.0.1' and int(params.get('port', 0)) == port,
                   'non_temporary_database_connection_blocked')
            ensure(params.get('dbname', params.get('database')) == engine.url.database,
                   'non_temporary_database_name_blocked')
            return original_connect(*args, **kwargs)
        monkeypatch.setattr(psycopg2, 'connect', guarded_connect)

        stage = 'build_fastapi_configuration'
        config = read_private_settings(engine.url.render_as_string(hide_password=False), prefix)
        report.update(configured_prefix_valid=True, local_admin_enabled=config.admin_enabled,
                      authentication='X-Admin-Key', temporary_prefix_exclusive=True)
        application = create_app(config)
        ensure(not application.dependency_overrides, 'unexpected_dependency_override')
        storage = application.state.image_storage
        guard = ScopedR2Client(storage._client_factory(), config.r2_bucket, prefix)
        storage._client = guard
        stage = 'initial_r2_prefix_check'
        ensure(not storage._call('list_objects_v2', Prefix=prefix + '/', MaxKeys=1).get('Contents'),
               'temporary_prefix_not_empty')

        stage = 'seed_temporary_database'
        now = datetime.now(timezone.utc)
        with application.state.session_factory.begin() as db:
            product = Product(loyverse_id='synthetic-product-' + uuid4().hex,
                              name='Producto sintetico de integracion R2', description='Solo prueba desechable',
                              is_active=True, is_available=True, synced_at=now)
            variant = ProductVariant(product=product, loyverse_id='synthetic-variant-' + uuid4().hex,
                                     name='Variante sintetica', price='1.00', is_available=True,
                                     is_commercially_available=True, synced_at=now)
            db.add(variant)
            db.flush()
            product_id, variant_id = product.product_id, variant.product_variant_id
        report.update(synthetic_products=1, synthetic_variants=1)

        source = BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text('probe', 'synthetic-disposable-image')
        Image.new('RGB', (16, 12), (31, 102, 73)).save(source, format='PNG', pnginfo=info)
        processed, extension = validate_image(source.getvalue())
        expected_hash = sha256(processed).digest()
        ensure(extension == 'png', 'unexpected_processed_format')
        report.update(processed_image_bytes=len(processed),
                      processing_removed_metadata=expected_hash != sha256(source.getvalue()).digest())
        path = f'/api/admin/variants/{variant_id}/images'
        headers = {'X-Admin-Key': config.admin_api_key.get_secret_value()}

        # ASGI real: middleware, dependencias, servicios, commits y lector de
        # medios sin sustituciones. Solo el transporte R2 tiene el guard de alcance.
        with TestClient(application, base_url='http://localhost', client=('127.0.0.1', 51001)) as client:
            stage = 'authentication_check'
            ensure(client.get(path).status_code == 401, 'missing_key_not_rejected')
            ensure('post' in client.get('/openapi.json').json()['paths'][path.replace(str(variant_id), '{variant_id}', 1)],
                   'upload_route_disabled')
            report['admin_authentication_verified'] = True
            stage = 'post_upload'
            response = client.post(path, headers=headers, files={'file': ('probe.png', source.getvalue(), 'image/png')},
                                   data={'display_order': '7'})
            report['post_status'] = response.status_code
            ensure(response.status_code == 201, 'post_did_not_return_201')
            image = response.json()
            ensure(image.get('product_variant_id') == variant_id and image.get('display_order') == 7,
                   'upload_contract_mismatch')
            ensure(image['image_url'].startswith(f'/media/products/{product_id}/variants/{variant_id}/'),
                   'public_url_contract_changed')
            image_id, image_url = image['product_image_id'], image['image_url']
            key = storage.key_for(image_url)
            stage = 'verify_committed_postgresql_reference'
            with application.state.session_factory() as db:
                row = db.get(ProductImage, image_id)
                ensure(row is not None and row.product_variant_id == variant_id and row.image_url == image_url
                       and row.display_order == 7, 'database_reference_mismatch')
            report['postgresql_reference_committed'] = True

            stage = 'verify_real_r2_object'
            head = storage._head(key)
            ensure(head is not None and head.get('ContentLength') == len(processed), 'r2_object_missing_or_wrong_size')
            ensure(head.get('Metadata', {}).get('sha256') == sha256(processed).hexdigest(), 'r2_sha256_metadata_mismatch')
            direct = storage.open_image(image_url)
            with direct.stream as stream:
                direct_hash = sha256(stream.read()).digest()
            ensure(direct_hash == expected_hash, 'real_r2_content_hash_mismatch')
            report['real_r2_object_verified'] = True

            stage = 'public_get'
            public = client.get(image_url)
            report['public_get_status'] = public.status_code
            ensure(public.status_code == 200, 'public_get_not_200')
            ensure(public.headers.get('content-type') == 'image/png', 'public_content_type_mismatch')
            ensure(int(public.headers.get('content-length', -1)) == len(processed), 'public_size_mismatch')
            ensure(sha256(public.content).digest() == expected_hash == direct_hash, 'public_processed_stored_hash_mismatch')
            report['sha256_processed_r2_public_equal'] = True

            stage = 'gallery_checks'
            gallery = client.get(path, headers=headers)
            ensure(gallery.status_code == 200 and gallery.json().get('images') == [image], 'admin_gallery_mismatch')
            detail = client.get(f'/api/menu/{product_id}')
            ensure(detail.status_code == 200, 'public_detail_failed')
            public_variant = next(v for v in detail.json()['product']['variants'] if v['product_variant_id'] == variant_id)
            ensure(len(public_variant['images']) == 1 and public_variant['images'][0]['product_image_id'] == image_id
                   and public_variant['images'][0]['display_order'] == 7, 'public_gallery_mismatch')
            report.update(admin_gallery_verified=True, public_gallery_verified=True, display_order_verified=7)

            stage = 'delete_via_endpoint'
            deletion = client.delete(f'{path}/{image_id}', headers=headers)
            report['delete_status'] = deletion.status_code
            ensure(deletion.status_code == 204, 'delete_not_204')
            with application.state.session_factory() as db:
                ensure(db.get(ProductImage, image_id) is None, 'database_reference_not_deleted')
                ensure(db.scalar(select(func.count()).select_from(ProductImage)) == 0, 'unexpected_remaining_image_references')
            ensure(storage._head(key) is None, 'real_r2_object_not_deleted')
            ensure(client.get(image_url).status_code == 404, 'deleted_public_image_still_visible')
            ensure(client.get(path, headers=headers).json().get('images') == [], 'gallery_not_empty_after_delete')
            report.update(postgresql_reference_deleted=True, real_r2_object_deleted=True,
                          public_get_after_delete_status=404, gallery_empty_after_delete=True)
            stage = 'final_r2_prefix_check'
            ensure(not storage._call('list_objects_v2', Prefix=prefix + '/', MaxKeys=1).get('Contents'),
                   'temporary_r2_objects_remain')
            report.update(r2_prefix_empty=True, temporary_objects_created=len(guard.confirmed),
                          only_own_objects_deleted=guard.deleted <= guard.attempted)
        report['status'] = 'success'
    except Exception as error:
        failure = type(error).__name__
        report.update(status='failed', failed_stage=stage, error_type=failure)
        if isinstance(error, ProbeFailure):
            report['check'] = str(error)
        if guard:
            report['provider_error'] = guard.errors[-1] if guard.errors else None
    finally:
        if guard:
            cleanup_errors = 0
            for key in sorted(guard.attempted - guard.deleted):
                if key in guard.uncertain:
                    continue
                try:
                    guard.delete_object(Bucket=guard.bucket, Key=key)
                except Exception:
                    cleanup_errors += 1
            report.update(r2_cleanup_errors=cleanup_errors, uncertain_puts=len(guard.uncertain))
            guard.close()
        if application is not None:
            application.state.session_factory.kw['bind'].dispose()
        report['private_dotenv_unchanged'] = sha256((PROJECT / '.env').read_bytes()).digest() == dotenv_hash
        # Este informe no contiene credenciales, cookies, URL SQL ni nombre de bucket.
        destination = Path(__file__).with_name('result.json')
        destination.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding='utf-8')
        print('R2_FASTAPI_RESULT=' + json.dumps(report, ensure_ascii=True, sort_keys=True), flush=True)
    if failure:
        pytest.fail('Real integration failed; see sanitized result.json', pytrace=False)

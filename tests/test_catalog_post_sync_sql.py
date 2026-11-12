"""SQL posterior sobre fixture sintetica temporal; sin migraciones ni roles nuevos."""

from app.models import catalog_metadata
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
from unittest.mock import MagicMock

from pydantic import SecretStr
import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.cli import sync_catalog
from app.db.connection import CloudDatabaseTarget
from app.models import Base
from app.services.catalog_manifest import catalog_manifest
from app.services.catalog_transform import transform_catalog
from app.services.sync_service import SyncService


@pytest.mark.asyncio
async def test_approved_initial_sync_uses_guard_in_actual_transaction(sync_db, commercial_snapshot, monkeypatch):
    import httpx
    from app.services.loyverse_client import LoyverseAPIClient

    def respond(request):
        if request.url.path.endswith('/stores'):
            return httpx.Response(200, json={'stores': [commercial_snapshot.store]})
        if request.url.path.endswith('/items'):
            return httpx.Response(200, json={'items': list(commercial_snapshot.items)})
        return httpx.Response(200, json={'modifiers': list(commercial_snapshot.modifiers)})

    monkeypatch.setattr(sync_catalog, 'LoyverseAPIClient', lambda **kwargs: LoyverseAPIClient(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    monkeypatch.setattr(sync_catalog, 'sessionmaker', lambda **kwargs: sync_db.sessions)
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = sync_db.engine.url.database
    approved = {'user': sync_db.engine.url.username, 'revision': '20261102_0003', 'catalog_manifest': catalog_manifest(
        transform_catalog(commercial_snapshot), commercial_snapshot.store,
    )}
    result = await sync_catalog.synchronize(sync_db.engine, target, 'test-store', SecretStr('fake-token'), approved)
    assert result['result']['products_processed'] == 3
    assert result['result']['assignments_inserted'] == 4


@pytest.mark.parametrize('reader_mode', [False, True])
@pytest.mark.parametrize('fault', [
    'none', 'database_price', 'manifest_name', 'manifest_price', 'manifest_parent', 'manifest_store',
])
def test_post_sql_checks_both_login_branches_and_rejects_price_drift(
    sync_test_database, commercial_snapshot, tmp_path, reader_mode, fault,
):
    if not os.getenv('MITBOL_SYNC_TEST_CLUSTER') and os.getenv('GITHUB_ACTIONS') != 'true':
        pytest.skip('Requiere el cluster temporal explícito o el servicio aislado de CI')
    engine = sync_test_database
    # Unicode real, comillas y texto similar a variables/dollar-quotes dentro de datos.
    commercial_snapshot.items[0]['item_name'] = "Pi\u00f1\u00f3n d'\u00c1vila :approved_manifest $$"
    commercial_snapshot.items[1]['variants'][0]['option1_value'] = 'Peque\u00f1a de lim\u00f3n'
    commercial_snapshot.modifiers[0]['name'] = 'Guarnici\u00f3n'
    plan = transform_catalog(commercial_snapshot)
    service = SyncService(sessionmaker(bind=engine), expected_database=engine.url.database, store_id='test-store')
    source = Path(__file__).resolve().parents[1] / 'docs/database/verify_neon_catalog_after_sync.sql'
    script = source.read_text(encoding='utf-8')
    # Sólo identidad en la copia temporal. El backend sin SSL ya no veta la auditoria.
    script = (script.replace('mitbol_cloud_dev', engine.url.database)
              .replace('neondb_owner', engine.url.username).replace('mitbol_render_reader', engine.url.username)
              .replace('9e30730f-a5b4-4485-8cc4-bc499cc6e239', 'test-store')
              .replace("'I.C.E'", "'Tienda simulada'"))
    path = tmp_path / 'post_sync_synthetic.sql'
    path.write_text(script, encoding='utf-8')
    psql = shutil.which('psql') or r'C:\Program Files\PostgreSQL\18\bin\psql.exe'
    child_env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    child_env.pop('MITBOL_SYNC_APPROVED_MANIFEST_B64', None)
    if engine.url.password:
        child_env['PGPASSWORD'] = engine.url.password  # Sólo contraseña efímera de CI.
    if reader_mode:
        child_env['PGOPTIONS'] = '-c default_transaction_read_only=on'
    try:
        service.persist_snapshot(commercial_snapshot)
        if fault == 'database_price':
            with engine.begin() as connection:
                connection.exec_driver_sql('UPDATE public."ProductVariants" SET price = price + 1')
        manifest = catalog_manifest(plan, commercial_snapshot.store)
        if fault == 'manifest_name':
            manifest['products'][0]['name'] += ' alterado'
        if fault == 'manifest_price':
            manifest['variants'][0]['price'] = '99999'
        if fault == 'manifest_parent':
            manifest['variants'][0]['product_loyverse_id'] = 'incorrect-parent'
        if fault == 'manifest_store':
            manifest['store']['id'] = 'incorrect-store'
        args = [
            psql, '-X', '-w', '-h', '127.0.0.1', '-p', str(engine.url.port), '-U', engine.url.username,
            '-d', engine.url.database, '-v', 'reader_mode=' + str(reader_mode).lower(), '-f', str(path),
        ]
        # Ejecuta las asignaciones REALES del script: ConvertTo-Json -> UTF8 -> Base64 -> getenv.
        helper = source.parent.parent / 'loyverse/verify_neon_after_sync.ps1'
        setup = re.search(r'try \{\n(.*?)    foreach \(\$login', helper.read_text(encoding='utf-8-sig'), re.S).group(1)
        encoded = base64.b64encode(json.dumps(manifest, ensure_ascii=False).encode('utf-8')).decode('ascii')
        program = "$ErrorActionPreference='Stop'\n"
        program += "$ApprovedManifest=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json\n"
        program += setup + '\n& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in args) + '\nexit $LASTEXITCODE\n'
        shell = shutil.which('powershell.exe') or shutil.which('pwsh')
        assert shell, 'Se requiere PowerShell para probar el transporte real'
        process = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command',
            '. ([scriptblock]::Create([Console]::In.ReadToEnd()))'], input=program,
            env=child_env, capture_output=True, text=True, encoding='utf-8', errors='strict', timeout=25)
        assert 'invalid multibyte' not in process.stderr and 'syntax error' not in process.stderr
        if fault != 'none':
            assert process.returncode == 3 and 'CATALOG_MISMATCH' in process.stdout
        else:
            assert process.returncode == 0, process.stderr
            assert 'catalog_data_verified_after_sync' in process.stdout
            assert 'MITBOL_CLIENT_CONNINFO_BEGIN' in process.stdout
            # El parser TLS de psql 18 se prueba aparte; CI puede tener cliente psql 16.
            assert 'tombstones_imported= 0' in process.stdout
            assert "Pi\u00f1\u00f3n d'\u00c1vila :approved_manifest $$" in process.stdout
            assert 'Peque\u00f1a de lim\u00f3n' in process.stdout
    finally:
        with engine.begin() as connection:
            for table in reversed(catalog_metadata.sorted_tables):
                connection.execute(delete(table))


@pytest.mark.parametrize('fault', ['database', 'user', 'reader_default', 'transaction_readonly'])
def test_post_sql_keeps_identity_and_readonly_guards(sync_test_database, commercial_snapshot, tmp_path, fault):
    if not os.getenv('MITBOL_SYNC_TEST_CLUSTER') and os.getenv('GITHUB_ACTIONS') != 'true':
        pytest.skip('Requiere el cluster temporal explícito o el servicio aislado de CI')
    engine = sync_test_database
    source = Path(__file__).resolve().parents[1] / 'docs/database/verify_neon_catalog_after_sync.sql'
    script = source.read_text(encoding='utf-8')
    if fault != 'database':
        script = script.replace('mitbol_cloud_dev', engine.url.database)
    if fault != 'user':
        script = script.replace('neondb_owner', engine.url.username)
    script = script.replace('mitbol_render_reader', engine.url.username)
    if fault == 'transaction_readonly':
        script = script.replace('REPEATABLE READ READ ONLY', 'REPEATABLE READ READ WRITE')
    path = tmp_path / 'identity_readonly_guard.sql'
    path.write_text(script, encoding='utf-8')
    child_env = {k:v for k,v in os.environ.items() if not k.startswith('PG')}
    child_env.update(LC_ALL='C', LC_MESSAGES='C')
    if engine.url.password:
        child_env['PGPASSWORD'] = engine.url.password
    child_env['MITBOL_SYNC_APPROVED_MANIFEST_B64'] = base64.b64encode(json.dumps(
        catalog_manifest(transform_catalog(commercial_snapshot), commercial_snapshot.store),
        ensure_ascii=False,
    ).encode('utf-8')).decode('ascii')
    psql = shutil.which('psql') or r'C:\Program Files\PostgreSQL\18\bin\psql.exe'
    process = subprocess.run([
        psql, '-X', '-w', '-h', '127.0.0.1', '-p', str(engine.url.port), '-U', engine.url.username,
        '-d', engine.url.database, '-v', 'reader_mode=' + str(fault == 'reader_default').lower(),
        '-f', str(path),
    ], env=child_env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25)
    assert process.returncode == 3 and 'POST_SYNC_IDENTITY' in process.stderr


@pytest.mark.parametrize('value,error', [
    (None, 'MISSING_APPROVED_MANIFEST'),
    ('', 'invalid input syntax for type json'),
    ('!!!', 'invalid symbol'),
    (base64.b64encode(b'\xff').decode('ascii'), 'invalid byte sequence'),
    (base64.b64encode(b'{invalid}').decode('ascii'), 'invalid input syntax for type json'),
])
def test_post_sql_rejects_missing_or_corrupt_manifest(sync_test_database, tmp_path, value, error):
    if not os.getenv('MITBOL_SYNC_TEST_CLUSTER') and os.getenv('GITHUB_ACTIONS') != 'true':
        pytest.skip('Requiere el cluster temporal explícito o el servicio aislado de CI')
    engine = sync_test_database
    source = Path(__file__).resolve().parents[1] / 'docs/database/verify_neon_catalog_after_sync.sql'
    script = (source.read_text(encoding='utf-8')
              .replace('mitbol_cloud_dev', engine.url.database)
              .replace('neondb_owner', engine.url.username))
    path = tmp_path / 'invalid_manifest.sql'
    path.write_text(script, encoding='ascii')
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('PG') and k != 'MITBOL_SYNC_APPROVED_MANIFEST_B64'}
    env.update(LC_ALL='C', LC_MESSAGES='C')
    if engine.url.password:
        env['PGPASSWORD'] = engine.url.password
    if value is not None:
        env['MITBOL_SYNC_APPROVED_MANIFEST_B64'] = value
    psql = shutil.which('psql') or r'C:\Program Files\PostgreSQL\18\bin\psql.exe'
    process = subprocess.run([
        psql, '-X', '-w', '-h', '127.0.0.1', '-p', str(engine.url.port), '-U', engine.url.username,
        '-d', engine.url.database, '-f', str(path),
    ], env=env, capture_output=True, text=True, encoding='utf-8', timeout=25)
    assert process.returncode == 3 and error in process.stderr
    assert 'catalog_data_verified_after_sync' not in process.stdout
    assert 'syntax error at or near ":"' not in process.stderr

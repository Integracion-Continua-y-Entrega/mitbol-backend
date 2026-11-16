"""Puente operativo 0007/0008 con LOGIN reales y proveedores simulados en PG18."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.db import writer_policy
from app.db.connection import DatabaseTargetError
from app.db.schema_compatibility import validate_bridge_schema
from app.db.worker_policy import validate_worker_connection
from app.cli.sync_discounts import preflight as discount_preflight
from app.models import Product
from app.services.discount_sync_service import DiscountSyncService
from app.services.recurring_catalog_sync import RecurringCatalogSync
from app.services.sync_jobs import SyncJobWorker
from test_admin_writer import execute_script, VERIFY
from test_neon_release_0007 import (
    release_backend as base_release_backend, writer_backend, writer_database,
    catalog_backend, auth_backend, auth_database, password_hash, restaurant,
)
import test_neon_release_0007 as release_tests
from test_recurring_catalog_sync import FakeCatalogAPI
from test_discount_migration import discount_snapshot

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / 'docs/database'


@pytest.fixture(params=['0007', '0008-before-delta', '0008-after-delta'])
def release_backend(base_release_backend, request):
    b = base_release_backend
    b.bridge_phase = request.param
    b.bridge_revision = '20261114_0007'
    if request.param != '0007':
        with b.roles.owner.begin() as db:
            db.exec_driver_sql((ROOT/'tests/fixtures/schema_0008_bridge.sql').read_text(encoding='utf-8'),
                               execution_options={'no_parameters': True})
        b.bridge_revision = '20261115_0008'
    if request.param == '0008-after-delta':
        with b.roles.owner.begin() as db:
            db.exec_driver_sql('GRANT SELECT(category_id,name,color,is_active) ON public."Categories" TO mitbol_render_reader')
            db.exec_driver_sql('GRANT SELECT(category_id) ON public."Products" TO mitbol_admin_writer')
            db.exec_driver_sql('GRANT SELECT ON public."Categories" TO mitbol_catalog_worker')
            db.exec_driver_sql('GRANT INSERT(loyverse_id,name,color,is_active,synced_at), UPDATE(name,color,is_active,synced_at) ON public."Categories" TO mitbol_catalog_worker')
            db.exec_driver_sql('GRANT SELECT(category_id),INSERT(category_id),UPDATE(category_id) ON public."Products" TO mitbol_catalog_worker')
            db.exec_driver_sql('GRANT USAGE ON SEQUENCE public."Categories_category_id_seq" TO mitbol_catalog_worker')
    try:
        yield b
    finally:
        if request.param != '0007':
            # Únicamente la base propia de la fixture, nunca una URL externa.
            with b.roles.owner.begin() as db:
                db.exec_driver_sql('ALTER TABLE public."Products" DROP COLUMN category_id')
                db.exec_driver_sql('DROP TABLE public."Categories"')
                db.exec_driver_sql("UPDATE public.alembic_version SET version_num='20261114_0007'")


def test_real_schema_and_three_strict_login_profiles(release_backend, monkeypatch):
    b = release_backend
    source = writer_policy.writer_policy_sql().replace('mitbol_cloud_dev', b.roles.owner.url.database)
    monkeypatch.setattr(writer_policy, 'writer_policy_sql', lambda: source)
    raw = b.roles.writer.raw_connection()
    try:
        writer_policy.validate_writer_connection(raw.driver_connection)
        with raw.cursor() as cursor:
            cursor.execute('SHOW transaction_read_only')
            assert cursor.fetchone()[0] == 'on'
    finally:
        raw.rollback();raw.close()
    with b.worker.connect() as db:
        db.exec_driver_sql('SET TRANSACTION READ ONLY')
        target = SimpleNamespace(validate_connection=lambda connection: None)
        validate_worker_connection(db, target)
        assert validate_bridge_schema(db) == b.bridge_revision
        db.rollback()
    reader = (SQL/'verify_render_reader.sql').read_text(encoding='utf-8')
    reader = '\n'.join(line for line in reader.splitlines() if not line.lstrip().startswith('\\'))
    execute_script(b.roles.reader, reader.replace('mitbol_cloud_dev', b.roles.owner.url.database))


def test_menu_auth_sessions_roles_csrf_and_persistent_queue(restaurant):
    b = restaurant
    assert b.client.get('/api/menu').status_code == 200
    release_tests.test_restaurant_queue_session_role_origin_csrf_and_history(b)
    assert b.client.get('/api/categories').status_code == 404
    # El ORM y los contratos HTTP del puente no incluyen la nueva función.
    assert 'category_id' not in Product.__table__.columns
    assert 'Categories' not in Product.metadata.tables


def test_r2_upload_read_delete_still_works_with_limited_roles(restaurant):
    release_tests.test_restaurant_r2_upload_public_read_and_delete_with_limited_writer(restaurant)


def test_recurring_worker_processes_queue_without_touching_categories(release_backend, commercial_snapshot):
    b = release_backend
    if b.bridge_revision == '20261115_0008':
        with b.roles.owner.begin() as db:
            db.exec_driver_sql("INSERT INTO public.\"Categories\"(category_id,loyverse_id,name,synced_at) VALUES(42,'kept-category','Kept',now())")
            db.exec_driver_sql('UPDATE public."Products" SET category_id=42 WHERE product_id=%s', (b.product_id,))
    touched = []
    def observe(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')):
            touched.append(statement)
    event.listen(b.worker, 'before_cursor_execute', observe)
    sessions = sessionmaker(bind=b.worker, expire_on_commit=False)
    target = SimpleNamespace(expected_database=b.worker.url.database,expected_host=b.worker.url.host,
                             validate_connection=lambda connection: None)
    runner = RecurringCatalogSync(sessions,expected_database=b.worker.url.database,
        expected_host=b.worker.url.host,store_id='test-store',auth_catalog=True,cloud_target=target)
    approval = dict(operation='catalog_recurrent_v1',approved=True,host=b.worker.url.host,
        database=b.worker.url.database,user='mitbol_catalog_worker',store_id='test-store',revision=b.bridge_revision)
    api = FakeCatalogAPI(commercial_snapshot)
    async def process():
        async with api.client() as client:
            return await SyncJobWorker(runner).run_once(client,approval=approval,scheduled=True)
    try:
        assert asyncio.run(process())['run']['status'] == 'succeeded'
        assert not any('Categories' in statement or 'category_id' in statement for statement in touched)
        assert not any('/categories' in str(request.url) for request in api.requests)
        if b.bridge_revision == '20261115_0008':
            with b.roles.owner.connect() as db:
                assert db.exec_driver_sql('SELECT category_id FROM public."Products" WHERE product_id=%s',(b.product_id,)).scalar_one() == 42
                assert db.exec_driver_sql('SELECT name FROM public."Categories" WHERE category_id=42').scalar_one() == 'Kept'
    finally:
        event.remove(b.worker,'before_cursor_execute',observe)


def test_cloud_discount_preflight_and_persistence_use_bridge_contract(release_backend):
    # El catálogo worker no recibe privilegios de descuentos. El operador de
    # esta base desechable permite ejercitar el servicio HU-009 por separado.
    engine = release_backend.roles.owner
    target = SimpleNamespace(expected_database=engine.url.database,
                             validate_connection=lambda connection: None)
    result = discount_preflight(engine, expected_database=engine.url.database, cloud_target=target)
    assert result['revision'] == release_backend.bridge_revision
    assert not result['writes'] and not result['loyverse_contacted']
    service = DiscountSyncService(sessionmaker(bind=engine), expected_database=engine.url.database,
        store_id='a3a56a06-d40c-4c07-931c-41abcbaaba12', cloud_target=target)
    assert service.persist_snapshot(discount_snapshot()) == 1
    with engine.connect() as db:
        assert db.exec_driver_sql('SELECT count(*) FROM public."Discounts"').scalar_one() == 1


@pytest.mark.parametrize('revision',['20261111_0006','20261116_0009','unknown'])
def test_bridge_rejects_unapproved_revisions(release_backend, revision):
    with release_backend.roles.owner.connect() as db:
        db.exec_driver_sql('UPDATE public.alembic_version SET version_num=%s',(revision,))
        with pytest.raises(DatabaseTargetError,match='exactamente'):
            validate_bridge_schema(db)
        db.rollback()


@pytest.mark.parametrize('mutation',[
    'DROP TABLE public."SyncRuns"',
    'ALTER TABLE public."Products" ALTER COLUMN name TYPE text',
    'ALTER TABLE public."ProductVariants" DROP CONSTRAINT "fk_ProductVariants_product_id"',
])
def test_bridge_rejects_incomplete_core_schema(release_backend, mutation):
    with release_backend.roles.owner.connect() as db:
        db.exec_driver_sql(mutation)
        with pytest.raises(DatabaseTargetError):
            validate_bridge_schema(db)
        db.rollback()


@pytest.mark.parametrize('mutation',[
    'DROP INDEX public."ix_Products_category_id"',
    'ALTER TABLE public."Categories" ALTER COLUMN name DROP NOT NULL',
    'ALTER TABLE public."Products" DROP CONSTRAINT "fk_Products_category_id"',
])
@pytest.mark.parametrize('release_backend', ['0008-before-delta', '0008-after-delta'], indirect=True)
def test_bridge_rejects_incomplete_0008_extension(release_backend, mutation):
    with release_backend.roles.owner.connect() as db:
        db.exec_driver_sql(mutation)
        with pytest.raises(DatabaseTargetError):
            validate_bridge_schema(db)
        db.rollback()


@pytest.mark.parametrize('profile,column', [
    ('writer', 'name'), ('worker', 'description'), ('reader', 'is_available'),
])
def test_bridge_profiles_reject_local_or_commercial_extra_writes(release_backend, profile, column):
    b = release_backend
    role = {'writer': 'mitbol_admin_writer', 'worker': 'mitbol_catalog_worker',
            'reader': 'mitbol_render_reader'}[profile]
    engine = b.worker if profile == 'worker' else getattr(b.roles, profile)
    file = {'writer': VERIFY, 'worker': SQL/'verify_catalog_worker.sql',
            'reader': SQL/'verify_render_reader.sql'}[profile]
    source = file.read_text(encoding='utf-8').replace('mitbol_cloud_dev', b.roles.owner.url.database)
    source = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('\\'))
    try:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql(f'GRANT UPDATE({column}) ON public."Products" TO {role}')
        with pytest.raises(DBAPIError, match='COLUMN|UNEXPECTED_OBJECT'):
            execute_script(engine, source)
    finally:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql(f'REVOKE UPDATE({column}) ON public."Products" FROM {role}')


@pytest.mark.parametrize('profile,grant,revoke', [
    ('writer', 'UPDATE(category_id)', 'UPDATE(category_id)'),
    ('worker', 'DELETE', 'DELETE'),
    ('reader', 'SELECT(loyverse_id)', 'SELECT(loyverse_id)'),
])
@pytest.mark.parametrize('release_backend', ['0008-before-delta', '0008-after-delta'], indirect=True)
def test_bridge_rejects_extra_category_privileges(release_backend, profile, grant, revoke):
    b = release_backend
    role = {'writer': 'mitbol_admin_writer', 'worker': 'mitbol_catalog_worker',
            'reader': 'mitbol_render_reader'}[profile]
    engine = b.worker if profile == 'worker' else getattr(b.roles, profile)
    file = {'writer': VERIFY, 'worker': SQL/'verify_catalog_worker.sql',
            'reader': SQL/'verify_render_reader.sql'}[profile]
    source = file.read_text(encoding='utf-8').replace('mitbol_cloud_dev', b.roles.owner.url.database)
    source = '\n'.join(line for line in source.splitlines() if not line.lstrip().startswith('\\'))
    table = 'Products' if profile == 'writer' else 'Categories'
    try:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql(f'GRANT {grant} ON public."{table}" TO {role}')
        with pytest.raises(DBAPIError, match='COLUMN|TABLE|CATEGORY_COLUMNS'):
            execute_script(engine, source)
    finally:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql(f'REVOKE {revoke} ON public."{table}" FROM {role}')

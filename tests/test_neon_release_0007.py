"""Ensayo 0005→0007, ACL reales y restaurant simulado, siempre en PG temporal."""

import asyncio
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, delete, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.db.connection import CloudDatabaseTarget, DatabaseTargetError
from app.main import create_app
from app.models import AuthBase, Base, Product, AuthRateLimit, AuthRateLimitPolicy, catalog_metadata, pre_categories_metadata
from app.models.sync_run import SyncBase, SyncRun
from app.services.recurring_catalog_sync import RecurringCatalogSync
from app.services import sync_jobs
from test_admin_writer import (writer_database, backend as writer_backend, denied,
                               execute_script, highlights_delta_sql, VERIFY)
from test_auth_admin_api import backend as catalog_backend, upload
from test_auth_api import backend as auth_backend, password_hash, login, csrf_headers
from test_auth_persistence import auth_database, config_for
from test_auth_cloud import render_config, login as cloud_login
from test_recurring_catalog_sync import FakeCatalogAPI
from test_r2_image_storage import ENDPOINT, r2


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "docs/database"
HEAD = "20261115_0008"


def approved(name, marker, value):
    return (SQL / name).read_text(encoding="utf-8").replace(
        "BEGIN;", f"BEGIN;\nSET LOCAL mitbol.{marker}='{value}';", 1)


def fingerprint(connection):
    # Sólo datos sintéticos; tampoco se publican hashes de contraseñas de prueba.
    result = {}
    for metadata in (catalog_metadata, AuthBase.metadata):
        for table in metadata.sorted_tables:
            rows = [dict(r) for r in connection.execute(
                select(table).order_by(*table.primary_key.columns)).mappings()]
            result[table.name] = sha256(json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest()
    result['sequences'] = connection.exec_driver_sql(
        "SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
    ).all()
    return result


def test_0005_to_0007_preserves_rows_and_rolls_back_ddl(writer_database):
    b = SimpleNamespace(roles=writer_database)
    with b.roles.owner.begin() as db:
        product_id = db.scalar(text('INSERT INTO "Products" (loyverse_id,name,synced_at) '
                                   "VALUES ('historic-photo','Anterior',now()) RETURNING product_id"))
        variant_id = db.scalar(text('INSERT INTO "ProductVariants" (loyverse_id,name,price,product_id,synced_at) '
                                   "VALUES ('historic-variant','Anterior',10,:product,now()) RETURNING product_variant_id"),
                               {'product': product_id})
        db.execute(text('INSERT INTO "ProductImages" (product_variant_id,image_url,display_order) VALUES (:variant,:url,0)'),
                   {'variant': variant_id, 'url': f'/media/products/{product_id}/variants/{variant_id}/'+'a'*32+'.png'})
    with b.roles.owner.connect() as db:
        before = fingerprint(db)
        assert MigrationContext.configure(db).get_current_heads() == ('20261106_0005',)
        command.upgrade(config_for(db), '20261111_0006')
        assert MigrationContext.configure(db).get_current_heads() == ('20261111_0006',)
        command.upgrade(config_for(db), "20261114_0007")
        after = fingerprint(db)
        # Sólo Discounts añade una secuencia; las existentes no se consumen.
        assert before['sequences'] == [r for r in after['sequences'] if r[0] != 'Discounts_discount_id_seq']
        assert {k:v for k,v in before.items() if k!='sequences'} == {k:v for k,v in after.items() if k!='sequences'}
        assert not compare_metadata(MigrationContext.configure(db), [pre_categories_metadata,AuthBase.metadata,SyncBase.metadata])
        db.rollback()  # Revierte ambas revisiones, tablas/índices y alembic_version.
    with b.roles.owner.connect() as db:
        assert MigrationContext.configure(db).get_current_heads() == ('20261106_0005',)
        assert 'Discounts' not in inspect(db).get_table_names()
        assert 'SyncRuns' not in inspect(db).get_table_names()
        assert fingerprint(db) == before
    with b.roles.owner.begin() as db:
        for table in reversed(catalog_metadata.sorted_tables):
            db.execute(table.delete())


@pytest.fixture
def release_backend(writer_backend):
    b = writer_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db), HEAD)
        # Reproduce exactamente las dos concesiones ausentes en Neon.
        db.exec_driver_sql('REVOKE UPDATE(is_featured,is_new) ON public."Products" FROM mitbol_admin_writer')
    execute_script(b.roles.owner, highlights_delta_sql('upgrade'))
    execute_script(b.roles.owner, approved('update_admin_writer_discounts.sql','approved_admin_write','hu009-reviewed'))
    execute_script(b.roles.owner, approved('update_admin_writer_sync.sql','approved_admin_write','hu010-reviewed'))
    execute_script(b.roles.owner, approved('prepare_catalog_worker.sql','approved_worker_write','hu010-worker-reviewed'))
    worker_password = 'synthetic-worker-fixture' if b.roles.owner.url.password else None
    if worker_password:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql("ALTER ROLE mitbol_catalog_worker PASSWORD 'synthetic-worker-fixture'")
    worker = create_engine(b.roles.owner.url.set(username='mitbol_catalog_worker',password=worker_password),hide_parameters=True)
    try:
        b.worker = worker
        yield b
    finally:
        worker.dispose()
        with b.roles.owner.begin() as db:
            db.exec_driver_sql('DROP OWNED BY mitbol_catalog_worker')
            db.exec_driver_sql('DROP ROLE mitbol_catalog_worker')
            db.exec_driver_sql('REVOKE SELECT(version_num) ON public.alembic_version FROM mitbol_admin_writer')
            db.exec_driver_sql('REVOKE SELECT(synced_at) ON public."ProductModifiers", public."ProductComplements" FROM mitbol_admin_writer')
            command.downgrade(config_for(db),'20261106_0005')


def test_exact_profiles_idempotent_delta_and_reader_unchanged(release_backend):
    b = release_backend
    execute_script(b.roles.writer, VERIFY.read_text(encoding='utf-8'))
    execute_script(b.worker,(SQL/'verify_catalog_worker.sql').read_text(encoding='utf-8'))
    execute_script(b.roles.owner, approved('update_admin_writer_sync.sql','approved_admin_write','hu010-reviewed'))
    execute_script(b.roles.writer, VERIFY.read_text(encoding='utf-8'))
    for table in ('SyncRuns','Discounts','PersonalAccounts'):
        denied(b.roles.reader,f'SELECT * FROM public."{table}"')
    with pytest.raises(DBAPIError,match='WORKER_EXISTS'):
        execute_script(b.roles.owner,approved('prepare_catalog_worker.sql','approved_worker_write','hu010-worker-reviewed'))


def test_cloud_worker_reuses_strict_policy_before_provider(release_backend):
    from app.db.worker_policy import validate_worker_connection, worker_policy_sql
    b=release_backend
    source=worker_policy_sql()
    assert 'GRANT ' not in source and 'CREATE ROLE' not in source
    for engine,allowed in ((b.worker,True),(b.roles.writer,False),(b.roles.owner,False)):
        def validate_target(connection):
            # Sólo se sustituye el transporte TLS en este PostgreSQL de loopback.
            assert connection.engine.url==engine.url
        with engine.connect() as db:
            db.exec_driver_sql('SET TRANSACTION READ ONLY')
            target=SimpleNamespace(validate_connection=validate_target)
            if allowed:
                validate_worker_connection(db,target)
                assert db.scalar(text('SHOW transaction_read_only'))=='on'
            else:
                with pytest.raises(DatabaseTargetError,match='perfil'):
                    validate_worker_connection(db,target)
            db.rollback()


@pytest.mark.parametrize('statement', [
    'UPDATE public."Products" SET description=description',
    'UPDATE public."Products" SET is_available=false',
    'UPDATE public."Products" SET is_featured=true',
    'UPDATE public."Products" SET is_new=false',
    'UPDATE public."ProductVariants" SET is_available=false',
    'UPDATE public."ProductVariants" SET product_id=product_id',
    'UPDATE public."Products" SET loyverse_id=loyverse_id',
    'DELETE FROM public."Products"', 'DELETE FROM public."ProductImages"',
    'DELETE FROM public."ProductTags"', 'SELECT * FROM public."PersonalAccounts"',
    'SELECT * FROM public."Discounts"', 'DELETE FROM public."SyncRuns"',
    'UPDATE public."SyncRuns" SET store_id=store_id',
    'CREATE TABLE public.worker_forbidden(id integer)',
    'SELECT nextval(\'public."ProductImages_product_image_id_seq"\')',
])
def test_worker_cannot_modify_local_or_admin_data(release_backend,statement):
    denied(release_backend.worker,statement)


@pytest.mark.parametrize('statement', [
    'UPDATE public."SyncRuns" SET status=status', 'DELETE FROM public."SyncRuns"',
    "INSERT INTO public.\"SyncRuns\"(run_id,store_id,source,status) VALUES(gen_random_uuid(),'test-store','manual','queued')",
    'UPDATE public."ProductVariants" SET price=price',
    'SELECT name FROM public."ProductModifiers"',
])
def test_web_writer_can_only_request_and_read_sync(release_backend,statement):
    denied(release_backend.roles.writer,statement)


def test_limited_worker_executes_real_upsert_and_atomic_queue_result(release_backend,commercial_snapshot):
    b = release_backend
    with b.roles.owner.begin() as db:
        db.execute(text('UPDATE public."Products" SET description=\'Local\',is_featured=true,is_new=false WHERE product_id=:id'),{'id':b.product_id})
    sessions = sessionmaker(bind=b.worker,expire_on_commit=False)
    def validate_local_transport(connection):
        # Simula exclusivamente TLS/host cloud; conserva LOGIN, SQL y ACL reales.
        assert connection.engine.url==b.worker.url
    target=SimpleNamespace(expected_database=b.worker.url.database,expected_host=b.worker.url.host,
                           validate_connection=validate_local_transport)
    runner = RecurringCatalogSync(sessions,expected_database=b.worker.url.database,
        expected_host=b.worker.url.host,store_id='test-store',auth_catalog=True,cloud_target=target)
    approval = {'operation':'catalog_recurrent_v1','approved':True,'host':b.worker.url.host,
                'database':b.worker.url.database,'user':'mitbol_catalog_worker','store_id':'test-store','revision':HEAD}
    async def run():
        async with FakeCatalogAPI(commercial_snapshot).client() as client:
            return await sync_jobs.SyncJobWorker(runner).run_once(client,approval=approval,scheduled=True)
    assert asyncio.run(run())['run']['status']=='succeeded'
    assert asyncio.run(run())['run']['status']=='succeeded'
    with sessions() as db:
        assert len(list(db.scalars(select(SyncRun))))==2
        row=db.get(Product,b.product_id)
        assert (row.description,row.is_featured,row.is_new)==('Local',True,False)
    execute_script(b.worker,(SQL/'verify_catalog_worker.sql').read_text(encoding='utf-8'))


@pytest.mark.parametrize('script,marker,value',[
    ('update_admin_writer_sync.sql','approved_admin_write','hu010-reviewed'),
    ('prepare_catalog_worker.sql','approved_worker_write','hu010-worker-reviewed'),
])
def test_sql_rejects_missing_approval_wrong_revision_and_web_login(writer_database,script,marker,value):
    b=writer_database
    with pytest.raises(DBAPIError,match='NOT_APPROVED'):
        execute_script(b.owner,(SQL/script).read_text(encoding='utf-8'))
    with pytest.raises(DBAPIError,match='WRONG_SCHEMA'):
        execute_script(b.owner,approved(script,marker,value))
    for engine in (b.writer,b.reader):
        with pytest.raises(DBAPIError):
            execute_script(engine,approved(script,marker,value))


@pytest.mark.parametrize('profile,grant,revoke',[
    ('worker','GRANT UPDATE(description) ON public."Products" TO mitbol_catalog_worker',
     'REVOKE UPDATE(description) ON public."Products" FROM mitbol_catalog_worker'),
    ('writer','GRANT UPDATE(status) ON public."SyncRuns" TO mitbol_admin_writer',
     'REVOKE UPDATE(status) ON public."SyncRuns" FROM mitbol_admin_writer'),
])
def test_verifiers_reject_extra_privileges(release_backend,profile,grant,revoke):
    b=release_backend
    engine=b.worker if profile=='worker' else b.roles.writer
    source=(SQL/'verify_catalog_worker.sql').read_text(encoding='utf-8') if profile=='worker' else VERIFY.read_text(encoding='utf-8')
    try:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql(grant)
        with pytest.raises(DBAPIError,match='COLUMN'):
            execute_script(engine,source)
    finally:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql(revoke)


def cloud_features(monkeypatch,**changes):
    values=dict(auth_sync_enabled=True,loyverse_store_id='test-store',auth_images_enabled=True,
        image_storage_provider='r2',r2_endpoint_url=ENDPOINT,r2_bucket='synthetic-images',
        r2_access_key_id='synthetic-access',r2_secret_access_key='synthetic-secret')
    return render_config(monkeypatch,**(values|changes))


@pytest.mark.parametrize('changes',[
    {'auth_enabled':False},{'auth_catalog_enabled':False},{'auth_cookie_secure':False},
    {'auth_cookie_partitioned':False},{'admin_enabled':True},{'loyverse_store_id':None},
    {'auth_origins':['http://localhost:5173']},{'image_storage_provider':'local'},
    {'r2_secret_access_key':None},{'auth_profile':'frontend_dev'},
])
def test_restaurant_features_keep_security_requirements(monkeypatch,changes):
    with pytest.raises((ValidationError,ValueError)):
        cloud_features(monkeypatch,**changes)


def test_restaurant_supports_explicit_features_without_provider_token(monkeypatch):
    config=cloud_features(monkeypatch)
    assert config.auth_sync_enabled and config.auth_images_enabled
    assert config.loyverse_access_token is None and not config.admin_enabled


@pytest.fixture
def restaurant(release_backend,monkeypatch):
    b=release_backend
    config=cloud_features(monkeypatch)
    storage=r2()
    with b.sessions.begin() as db:
        db.execute(delete(AuthRateLimit))
        db.execute(delete(AuthRateLimitPolicy))
    import app.main as main
    from app.db import auth_database as pools
    monkeypatch.setattr(main,'session_factory_for',lambda _:sessionmaker(bind=b.roles.reader))
    monkeypatch.setattr(main,'create_image_storage',lambda _:storage)
    monkeypatch.setattr(pools,'create_database_engine',lambda *a,**k:b.roles.writer)
    def validate_local_transport(self,connection):
        assert connection.engine.url==b.roles.writer.url
    monkeypatch.setattr(CloudDatabaseTarget,'validate_connection',validate_local_transport)
    original=sync_jobs.inspect_queue
    def inspect_local_transport(db,*,expected_database,expected_host,permission='read'):
        assert (expected_database,expected_host)==(config.expected_database,config.expected_database_host)
        return original(db,expected_database=b.roles.owner.url.database,
                        expected_host=b.roles.owner.url.host,permission=permission)
    monkeypatch.setattr(sync_jobs,'inspect_queue',inspect_local_transport)
    app=create_app(config)
    app.state.auth_service.clock=lambda:b.now.value
    try:
        with TestClient(app,base_url=config.auth_public_origin,headers={'Origin':config.auth_origins[0]}) as client:
            yield SimpleNamespace(original=b,app=app,client=client,config=config,storage=storage)
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(AuthRateLimit))
            db.execute(delete(AuthRateLimitPolicy))


def test_restaurant_queue_session_role_origin_csrf_and_history(restaurant):
    b=restaurant
    assert b.client.get('/api/admin/sync/status').status_code==401
    response=cloud_login(b,'staff')
    assert response.status_code==200
    assert b.client.get('/api/admin/sync/runs').status_code==403
    b.client.cookies.clear()
    response=cloud_login(b)
    assert response.status_code==200
    assert 'Partitioned' in response.headers['set-cookie']
    csrf={'X-CSRF-Token':response.json()['csrf_token']}
    assert b.client.post('/api/admin/sync/runs',json={}).status_code==403
    assert b.client.post('/api/admin/sync/runs',json={},headers=csrf|{'Origin':'https://evil.invalid'}).status_code==403
    assert b.client.get('/api/admin/sync/status').status_code==200
    accepted=b.client.post('/api/admin/sync/runs',json={},headers=csrf)
    assert accepted.status_code==202,accepted.text
    identifier=accepted.json()['run']['run_id']
    assert b.client.get(f'/api/admin/sync/runs/{identifier}').status_code==200
    assert len(b.client.get('/api/admin/sync/runs').json()['runs'])==1
    # El proceso HTTP sólo persistió la solicitud, sin proveedor ni ejecución.
    assert accepted.json()['run']['status']=='queued'
    assert not b.storage._client.calls


def test_restaurant_r2_upload_public_read_and_delete_with_limited_writer(restaurant):
    from io import BytesIO
    from PIL import Image
    b=restaurant
    response=cloud_login(b)
    assert response.status_code==200,response.text
    headers={'X-CSRF-Token':response.json()['csrf_token']}
    image=BytesIO()
    Image.new('RGB',(3,3),'orange').save(image,format='PNG')
    path=f'/api/admin/variants/{b.original.variant_id}/images'
    assert b.client.post(path,files={'file':('test.png',image.getvalue(),'image/png')}).status_code==403
    response=b.client.post(path,headers=headers,files={'file':('test.png',image.getvalue(),'image/png')})
    assert response.status_code==201,response.text
    stored=response.json()
    public=b.client.get(stored['image_url'],headers={'Origin':b.config.auth_origins[0]})
    assert public.status_code==200
    assert sha256(public.content).digest()==sha256(next(iter(b.storage._client.objects.values()))[0]).digest()
    assert b.client.delete(path+'/'+str(stored['product_image_id']),headers=headers).status_code==204
    assert b.client.get(stored['image_url']).status_code==404
    assert not b.storage._client.objects

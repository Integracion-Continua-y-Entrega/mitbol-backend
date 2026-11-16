"""0008, Loyverse simulado, API y LOGINs reales en PostgreSQL temporal."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.main import create_app
from app.models import AuthBase, Base, CommercialCategory, Product, ProductImage, ProductTag, ProductVariant, Tag
from app.models.sync_run import SyncBase, SyncRun
from app.services import sync_jobs
from app.services.catalog_errors import CatalogValidationError
from app.services.catalog_transform import transform_catalog
from app.services.recurring_catalog_sync import RecurringCatalogSync
from test_admin_writer import denied, execute_script
from test_auth_persistence import auth_database, config_for
from test_recurring_catalog_sync import FakeCatalogAPI

ROOT = Path(__file__).resolve().parents[1]
HEAD = "20261115_0008"


@pytest.fixture
def category_database(auth_database):
    # Reutiliza el clúster ya contrastado por auth_database; nunca lee .env.
    assert auth_database.url.host in {"127.0.0.1", "::1"}
    assert auth_database.url.username == "mitbol_ci"
    name = "mitbol_test_auth_" + uuid4().hex
    control = create_engine(auth_database.url.set(database="template1"), isolation_level="AUTOCOMMIT")
    with control.connect() as db:
        db.exec_driver_sql(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
    engine = create_engine(auth_database.url.set(database=name), hide_parameters=True)
    try:
        with engine.begin() as db:
            command.upgrade(config_for(db), "20261114_0007")
        yield engine
    finally:
        engine.dispose()
        with control.connect() as db:
            db.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        control.dispose()


@pytest.fixture
def categories(category_database, commercial_snapshot, tmp_path):
    engine = category_database
    with engine.begin() as db:
        command.upgrade(config_for(db), HEAD)
    items = [{"category_id": None, **deepcopy(row)} for row in commercial_snapshot.items]
    items[0]["category_id"] = "category-food"
    snapshot = replace(commercial_snapshot, items=tuple(items), categories=(
        {"id": "category-food", "name": "Comida", "color": "GREEN", "deleted_at": None},
    ))
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    runner = RecurringCatalogSync(sessions, expected_database=engine.url.database,
        expected_host=engine.url.host, store_id="test-store", auth_catalog=True)
    approval = dict(operation="catalog_recurrent_v1", approved=True, host=engine.url.host,
        database=engine.url.database, user="mitbol_ci", store_id="test-store", revision=HEAD)
    config = Settings(_env_file=None, app_env="test", database_url=engine.url.render_as_string(hide_password=False),
                      admin_enabled=False, auth_enabled=False, image_storage_provider="local",
                      image_storage_root=tmp_path / "images")
    with TestClient(create_app(config)) as client:
        yield SimpleNamespace(engine=engine, sessions=sessions, runner=runner, approval=approval,
                              snapshot=snapshot, client=client)


def run(b, snapshot=None, *, dry_run=False, fault=None):
    api = FakeCatalogAPI(snapshot or b.snapshot, fault)
    async def execute():
        async with api.client() as client:
            return await b.runner.run(client, dry_run=dry_run, approval=b.approval)
    return asyncio.run(execute())


def sequence_state(engine):
    with engine.connect() as db:
        return db.exec_driver_sql("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").all()


def test_upgrade_0007_preserves_twenty_tables_rows_indexes_sequences(category_database):
    with category_database.begin() as db:
        db.exec_driver_sql('INSERT INTO "Products" (loyverse_id,name,description,synced_at,is_available,is_featured,is_new) '
                           "VALUES ('old-product','Anterior','Local',now(),false,true,false)")
        old = MetaData()
        old.reflect(db)
        tables = set(old.tables)
        assert len(tables) == 20 and "categories" not in tables
        rows = {name: db.execute(select(table)).all() for name, table in old.tables.items() if name != 'alembic_version'}
        indexes = db.exec_driver_sql("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY indexname").all()
        sequences = db.exec_driver_sql("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").all()
        command.upgrade(config_for(db), HEAD)
        assert MigrationContext.configure(db).get_current_heads() == (HEAD,)
        assert set(inspect(db).get_table_names()) == tables | {"Categories"}
        for name, table in old.tables.items():
            if name != 'alembic_version':
                assert db.execute(select(table)).all() == rows[name]
        assert set(indexes) <= set(db.exec_driver_sql("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public'").all())
        assert set(sequences) <= set(db.exec_driver_sql("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public'").all())
        assert db.scalar(text('SELECT category_id FROM "Products"')) is None
        assert not compare_metadata(MigrationContext.configure(db, opts={"compare_type": True}),
                                    [Base.metadata, AuthBase.metadata, SyncBase.metadata])


def test_first_repeat_preview_and_category_price_names(categories):
    b = categories
    state = sequence_state(b.engine)
    assert run(b, dry_run=True)["changes"]["categories"]["new"] == 1
    assert sequence_state(b.engine) == state
    assert run(b)["changes"]["categories"]["new"] == 1
    with b.sessions() as db:
        category_id = db.scalar(select(CommercialCategory.category_id))
        product_id = db.scalar(select(Product.product_id).where(Product.loyverse_id == b.snapshot.items[0]['id']))
    assert run(b)["changes"]["categories"]["unchanged"] == 1
    renamed = replace(b.snapshot, categories=({"id":"category-food","name":"Alimentos","color":"RED"},))
    assert run(b, renamed)["changes"]["categories"]["updated"] == 1
    with b.sessions() as db:
        assert db.scalar(select(func.count()).select_from(CommercialCategory)) == 1
        assert db.get(CommercialCategory, category_id).name == "Alimentos"
        assert db.get(Product, product_id).category_id == category_id


def test_new_category_reassignment_explicit_null_and_local_preservation(categories):
    b = categories
    run(b)
    with b.sessions.begin() as db:
        product = db.scalar(select(Product).where(Product.loyverse_id == b.snapshot.items[0]['id']))
        product.description, product.is_available, product.is_featured, product.is_new = "Local", False, True, False
        variant = product.variants[0]
        variant.is_available = False
        image = ProductImage(product_variant_id=variant.product_variant_id,
            image_url=f"/media/products/{product.product_id}/variants/{variant.product_variant_id}/"+'a'*32+'.png', display_order=2)
        tag = Tag(name="Local")
        db.add_all([image,tag]);db.flush()
        db.add(ProductTag(product_variant_id=variant.product_variant_id,tag_id=tag.tag_id))
        identifier, image_id, tag_id = product.product_id, image.product_image_id, tag.tag_id
    items = deepcopy(b.snapshot.items);items[0]['category_id'] = 'category-drinks'
    snapshot = replace(b.snapshot,items=items,categories=(*b.snapshot.categories,
        {"id":"category-drinks","name":"Bebidas","color":"BLUE"}))
    assert run(b,snapshot)['changes']['products']['updated'] == 1
    with b.sessions() as db:
        product = db.get(Product,identifier)
        assert product.category.loyverse_id == 'category-drinks'
        assert (product.description,product.is_available,product.is_featured,product.is_new)==('Local',False,True,False)
        assert not product.variants[0].is_available
        assert db.get(ProductImage,image_id).display_order == 2 and db.get(Tag,tag_id).name == 'local'
    # Una categoría con producto agotado sigue visible; una categoría vacía no.
    assert [row['name'] for row in b.client.get('/api/categories').json()['categories']] == ['Bebidas']
    items[0]['category_id'] = None
    run(b,replace(snapshot,items=items))
    with b.sessions() as db: assert db.get(Product,identifier).category_id is None


@pytest.mark.parametrize('mutation', ['missing-field','unknown-id','bad-color','bad-name','duplicate','bad-deletion'])
def test_invalid_categories_abort_entire_batch(categories,mutation):
    b=categories;run(b)
    snapshot=deepcopy(b.snapshot);items=list(snapshot.items);records=list(snapshot.categories)
    if mutation=='missing-field':items[0].pop('category_id')
    elif mutation=='unknown-id':items[0]['category_id']='unobserved'
    elif mutation=='bad-color':records[0]['color']=[]
    elif mutation=='bad-name':records[0]['name']='x'*65
    elif mutation=='duplicate':records.append(deepcopy(records[0]))
    else: records[0]['deleted_at']='invalid'
    with pytest.raises(CatalogValidationError): run(b,replace(snapshot,items=tuple(items),categories=tuple(records)))
    with b.sessions() as db:
        assert db.scalar(select(CommercialCategory.name))=='Comida'
        assert db.scalar(select(func.count()).select_from(CommercialCategory))==1


def test_explicit_deletion_preserves_products_and_absence_does_not_delete(categories):
    b=categories;run(b)
    missing=replace(b.snapshot,items=(),categories=())
    assert run(b,missing)['changes']['categories']['not_returned']==1
    with b.sessions() as db:
        category_id=db.scalar(select(CommercialCategory.category_id))
        count=db.scalar(select(func.count()).select_from(Product))
    deleted=replace(missing,categories=({'id':'category-food','deleted_at':'2026-11-15T00:00:00Z'},))
    assert run(b,deleted)['changes']['categories']['retired']==1
    with b.sessions() as db:
        assert not db.get(CommercialCategory,category_id).is_active
        assert db.scalar(select(func.count()).select_from(Product))==count
        assert db.scalar(select(Product.category_id).where(Product.loyverse_id==b.snapshot.items[0]['id']))==category_id
    assert b.client.get('/api/categories').json()=={'categories':[]}
    assert all(row['category'] is None for row in b.client.get('/api/menu').json()['products'])
    with b.engine.begin() as db:
        with pytest.raises(IntegrityError):db.execute(CommercialCategory.__table__.delete())
    assert run(b)['changes']['categories']['updated'] == 1
    with b.sessions() as db:
        assert db.get(CommercialCategory, category_id).is_active
        assert db.scalar(select(func.count()).select_from(CommercialCategory)) == 1


def test_category_filter_combines_name_tags_without_duplicate_variants(categories):
    b=categories;run(b)
    with b.sessions.begin() as db:
        product=db.scalar(select(Product).where(Product.loyverse_id==b.snapshot.items[0]['id']))
        category_id,identifier,name=product.category_id,product.product_id,product.name
        tag=Tag(name='Filtro local');db.add(tag);db.flush();tag_id=tag.tag_id
        db.add_all([ProductTag(product_variant_id=v.product_variant_id,tag_id=tag_id) for v in product.variants])
    available=b.client.get('/api/categories').json()['categories']
    assert available==[{'category_id':category_id,'name':'Comida','color':'GREEN'}]
    response=b.client.get('/api/menu',params=[('category_id',category_id),('q',name),('tag_id',tag_id)])
    assert response.status_code==200
    assert [p['product_id'] for p in response.json()['products']]==[identifier]
    detail=b.client.get(f'/api/menu/{identifier}').json()['product']
    assert detail['category']==available[0] and detail['variants']
    assert b.client.get('/api/menu',params={'category_id':99999}).json()['products']==[]
    plain=b.client.get('/api/menu',params={'uncategorized':'true'}).json()['products']
    assert all(p['category'] is None and p['product_id']!=identifier for p in plain)
    for params in ({'category_id':0},{'category_id':'bad'},{'category_id':category_id,'uncategorized':True}):
        assert b.client.get('/api/menu',params=params).status_code==422


def test_failed_category_page_and_rollback_keep_previous_data(categories):
    b=categories;run(b)
    def fail(request,key):
        if key=='categories':return httpx.Response(200,json={'categories':[],'cursor':'repeated'})
    from app.services.loyverse_client import LoyverseResponseError
    with pytest.raises(LoyverseResponseError):run(b,fault=fail)
    with b.sessions() as db:assert db.scalar(select(CommercialCategory.name))=='Comida'
    from app.services.sync_service import SyncService
    from app.services.catalog_errors import CatalogPersistenceError
    def fail_after(db,result):raise IntegrityError('synthetic',None,ValueError('synthetic'))
    service=SyncService(b.sessions,expected_database=b.engine.url.database,store_id='test-store',
        auth_catalog=True,after_write=fail_after)
    changed=replace(b.snapshot,categories=({'id':'category-food','name':'Rollback','color':None},))
    with pytest.raises(CatalogPersistenceError):service.persist_snapshot(changed)
    with b.sessions() as db:assert db.scalar(select(CommercialCategory.name))=='Comida'


def test_hu010_queue_records_category_changes(categories):
    b=categories
    async def process():
        async with FakeCatalogAPI(b.snapshot).client() as client:
            return await sync_jobs.SyncJobWorker(b.runner).run_once(client,approval=b.approval,scheduled=True)
    assert asyncio.run(process())['run']['status']=='succeeded'
    assert asyncio.run(process())['run']['status']=='succeeded'
    with b.sessions() as db:
        rows=list(db.scalars(select(SyncRun).order_by(SyncRun.created_at)))
        assert len(rows)==2
        assert rows[0].result['changes']['categories']['new']==1
        assert rows[1].result['changes']['categories']['unchanged']==1


def test_minimal_reader_admin_worker_login_profiles(categories):
    b=categories;run(b)
    sql=ROOT/'docs/database'
    reader=(sql/'prepare_render_reader.sql').read_text(encoding='utf8').replace('mitbol_cloud_dev',b.engine.url.database).replace('neondb_owner','mitbol_ci')
    execute_script(b.engine,reader)
    engines=[]
    try:
        execute_script(b.engine,(sql/'prepare_admin_writer_local.sql').read_text(encoding='utf8'))
        worker=(sql/'prepare_catalog_worker.sql').read_text(encoding='utf8').replace('BEGIN;',"BEGIN; SET LOCAL mitbol.approved_worker_write='hu010-worker-reviewed';",1)
        execute_script(b.engine,worker)
        for role in ('mitbol_render_reader','mitbol_admin_writer','mitbol_catalog_worker'):
            if b.engine.url.password:
                with b.engine.begin() as db:db.exec_driver_sql(f"ALTER ROLE {role} PASSWORD 'synthetic-category-fixture'")
            engines.append(create_engine(b.engine.url.set(username=role,password='synthetic-category-fixture' if b.engine.url.password else None),hide_parameters=True))
        public,admin,worker=engines
        reader_verify=(sql/'verify_render_reader.sql').read_text(encoding='utf8').replace('mitbol_cloud_dev',b.engine.url.database)
        # Python driver cannot consume psql meta-commands.
        execute_script(public,'\n'.join(line for line in reader_verify.splitlines() if not line.startswith('\\')))
        execute_script(admin,(sql/'verify_admin_writer_local.sql').read_text(encoding='utf8'))
        execute_script(worker,(sql/'verify_catalog_worker.sql').read_text(encoding='utf8'))
        with sessionmaker(bind=public)() as db:
            from app.services.menu_service import get_categories,get_menu
            assert get_categories(db).categories
            assert get_menu(db).products
        for engine in engines:
            denied(engine,'UPDATE public."Categories" SET category_id=category_id',
                   code='25006' if engine is public else '42501')
        for engine in (public,admin):
            denied(engine,'UPDATE public."Products" SET category_id=NULL',
                   code='25006' if engine is public else '42501')
            denied(engine,'SELECT loyverse_id FROM public."Categories"')
        for statement in ('UPDATE public."Products" SET description=description',
                          'UPDATE public."Products" SET is_available=is_available',
                          'UPDATE public."Products" SET is_featured=is_featured',
                          'DELETE FROM public."ProductImages"','DELETE FROM public."ProductTags"',
                          'CREATE TABLE public.forbidden_category_test(id int)'):
            denied(worker,statement)
        with worker.begin() as db:
            db.exec_driver_sql('UPDATE public."Categories" SET name=name')
            db.exec_driver_sql('UPDATE public."Products" SET category_id=category_id')
        source = (sql / 'update_catalog_categories.sql').read_text(encoding='utf8')
        with pytest.raises(DBAPIError, match='CATEGORIES_NOT_APPROVED'):
            execute_script(b.engine, source)
        delta = source.replace('BEGIN;', "BEGIN; SET LOCAL mitbol.approved_category_write='hu008-categories-reviewed';", 1)
        for engine in (admin, worker):
            with pytest.raises(DBAPIError, match='CATEGORIES_WRONG_TARGET_OR_OPERATOR'):
                execute_script(engine, delta)
        execute_script(b.engine,delta);execute_script(b.engine,delta)
        execute_script(worker,(sql/'verify_catalog_worker.sql').read_text(encoding='utf8'))
    finally:
        for engine in engines:engine.dispose()
        with b.engine.begin() as db:
            for role in ('mitbol_catalog_worker','mitbol_admin_writer','mitbol_render_reader'):
                if db.scalar(text('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)'),{'role':role}):
                    db.exec_driver_sql(f'DROP OWNED BY {role}');db.exec_driver_sql(f'DROP ROLE {role}')

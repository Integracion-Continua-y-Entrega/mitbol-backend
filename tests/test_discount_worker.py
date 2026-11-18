"""Perfil HU-009 con LOGIN real, únicamente en PostgreSQL temporal del laboratorio."""

from dataclasses import replace
from pathlib import Path

from alembic import command
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.cli.sync_discounts import preflight
from app.db.discount_worker_policy import discount_worker_policy_sql
from app.services.discount_probe import DiscountSnapshot
from app.services.discount_sync_service import DiscountSyncService
from app.services.discount_transform import parse_discount
from test_admin_writer import execute_script, denied
from test_discount_lifecycle import database, source, STORE
from test_discount_migration import staged_config


SQL = Path(__file__).resolve().parents[1] / 'docs/database'
PREPARE = (SQL / 'prepare_discount_worker.sql').read_text(encoding='utf-8')
VERIFY = (SQL / 'verify_discount_worker.sql').read_text(encoding='utf-8')
APPROVED = PREPARE.replace('BEGIN;', "BEGIN;\nSET LOCAL mitbol.approved_discount_worker_write='hu009-discount-worker-reviewed';", 1)


@pytest.fixture
def discount_lab(database, staged_config):
    with database.begin() as db:
        staged_config.attributes['connection'] = db
        command.upgrade(staged_config, '20261115_0008')
        # Sólo ESTA base vacía, creada por la fixture existente y validada en loopback.
        assert database.url.host == '127.0.0.1' and database.url.database.startswith('mitbol_test_hu008_')
        db.exec_driver_sql(f'REVOKE CREATE,TEMPORARY ON DATABASE "{database.url.database}" FROM PUBLIC')
        db.exec_driver_sql('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    try:
        yield database
    finally:
        with database.begin() as db:
            if db.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_discount_worker')")):
                db.exec_driver_sql('DROP OWNED BY mitbol_discount_worker')
                db.exec_driver_sql('DROP ROLE mitbol_discount_worker')


@pytest.fixture
def worker(discount_lab):
    owner = discount_lab
    execute_script(owner, APPROVED)
    if owner.url.password:
        with owner.begin() as db:
            db.exec_driver_sql("ALTER ROLE mitbol_discount_worker PASSWORD 'synthetic-discount-worker-fixture'")
    engine = create_engine(owner.url.set(username='mitbol_discount_worker',
        password='synthetic-discount-worker-fixture' if owner.url.password else None), hide_parameters=True)
    try:
        yield engine, owner
    finally:
        engine.dispose()


def test_real_login_exact_profile_and_preflight_do_not_consume_sequence(worker, monkeypatch):
    engine, owner = worker
    monkeypatch.delenv('LOYVERSE_ACCESS_TOKEN', raising=False)
    with owner.connect() as db:
        before = db.exec_driver_sql('SELECT last_value,is_called FROM public."Discounts_discount_id_seq"').one()
    execute_script(engine, VERIFY)
    # Mismo bloque que ejecutará el CLI automático, con LOGIN local independiente.
    with engine.connect() as db, db.begin():
        db.exec_driver_sql('SET TRANSACTION READ ONLY')
        db.exec_driver_sql(discount_worker_policy_sql(), execution_options={'no_parameters': True})
    result = preflight(engine, expected_database=owner.url.database)
    assert result['revision'] == '20261115_0008' and result['writes'] is False
    assert result['loyverse_contacted'] is False and result['discount_count'] == 0
    with engine.connect() as db:
        assert db.exec_driver_sql('SELECT current_user,session_user').one() == ('mitbol_discount_worker',) * 2
    with owner.connect() as db:
        assert db.exec_driver_sql('SELECT last_value,is_called FROM public."Discounts_discount_id_seq"').one() == before


def test_minimal_worker_can_upsert_without_replacing_local_identity(worker):
    engine, owner = worker
    runner = DiscountSyncService(sessionmaker(bind=engine), expected_database=owner.url.database, store_id=STORE)
    record = parse_discount(source())
    assert runner.persist_snapshot(DiscountSnapshot((record,), True)) == 1
    with engine.connect() as db:
        first = db.exec_driver_sql('SELECT * FROM public."Discounts"').mappings().one()
    assert runner.persist_snapshot(DiscountSnapshot((replace(record,name='Actualizado'),), True)) == 1
    with engine.connect() as db:
        second = db.exec_driver_sql('SELECT * FROM public."Discounts"').mappings().one()
    assert second['name'] == 'Actualizado'
    assert (second['discount_id'],second['created_at']) == (first['discount_id'],first['created_at'])
    execute_script(engine, VERIFY)


@pytest.mark.parametrize('statement', [
    'SELECT * FROM public."Products"',
    'UPDATE public."Products" SET is_featured=true WHERE false',
    'UPDATE public."Categories" SET name=\'Prohibido\' WHERE false',
    'UPDATE public."ProductVariants" SET price=1 WHERE false',
    'UPDATE public."ProductImages" SET display_order=1 WHERE false',
    'DELETE FROM public."Tags" WHERE false',
    'UPDATE public."SyncRuns" SET status=\'running\' WHERE false',
    'SELECT * FROM public."Persons"',
    'UPDATE public.alembic_version SET version_num=\'20261114_0007\' WHERE false',
    'UPDATE public."Discounts" SET loyverse_id=\'Prohibido\' WHERE false',
    'UPDATE public."Discounts" SET discount_id=1 WHERE false',
    'UPDATE public."Discounts" SET created_at=now() WHERE false',
    'DELETE FROM public."Discounts" WHERE false',
    'TRUNCATE public."Discounts"',
    'INSERT INTO public."Discounts" (discount_id) VALUES (900)',
    'SELECT last_value FROM public."Discounts_discount_id_seq"',
    "SELECT setval('public.\"Discounts_discount_id_seq\"',900)",
    'CREATE TABLE public.discount_worker_forbidden (id integer)',
    'CREATE TEMP TABLE discount_worker_forbidden (id integer)',
    'CREATE SCHEMA discount_worker_forbidden',
])
def test_unrelated_data_protected_columns_and_ddl_are_denied(worker, statement):
    denied(worker[0], statement)


@pytest.mark.parametrize('extra', [
    'GRANT SELECT ON public."Products" TO mitbol_discount_worker',
    'GRANT UPDATE(loyverse_id) ON public."Discounts" TO mitbol_discount_worker',
    'GRANT INSERT ON public."Discounts" TO mitbol_discount_worker',
    'GRANT SELECT ON public."Discounts" TO mitbol_discount_worker',
    'GRANT SELECT ON SEQUENCE public."Discounts_discount_id_seq" TO mitbol_discount_worker',
    'GRANT CREATE ON SCHEMA public TO mitbol_discount_worker',
    'GRANT pg_read_all_data TO mitbol_discount_worker',
    'ALTER ROLE mitbol_discount_worker INHERIT',
    'GRANT UPDATE(name) ON public."Discounts" TO mitbol_discount_worker WITH GRANT OPTION',
])
def test_verifier_rejects_excess_permissions(worker, extra):
    engine, owner = worker
    with owner.begin() as db:
        db.exec_driver_sql(extra)
    with pytest.raises(DBAPIError, match='DISCOUNT_WORKER_'):
        execute_script(engine, VERIFY)


@pytest.mark.parametrize('case', ['approval','revision','public_table','public_column','public_temp','public_routine','default_acl','rls'])
def test_guard_fails_before_role_creation(discount_lab, case):
    owner = discount_lab
    script = APPROVED
    with owner.begin() as db:
        if case == 'approval': script = PREPARE
        elif case == 'revision': db.exec_driver_sql("UPDATE public.alembic_version SET version_num='20261114_0007'")
        elif case == 'public_table': db.exec_driver_sql('GRANT SELECT ON public."Products" TO PUBLIC')
        elif case == 'public_column': db.exec_driver_sql('GRANT SELECT(name) ON public."Products" TO PUBLIC')
        elif case == 'public_temp': db.exec_driver_sql(f'GRANT TEMPORARY ON DATABASE "{owner.url.database}" TO PUBLIC')
        elif case == 'public_routine': db.exec_driver_sql('CREATE FUNCTION public.discount_worker_unsafe() RETURNS integer LANGUAGE sql AS \'SELECT 1\'')
        elif case == 'default_acl': db.exec_driver_sql('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC')
        elif case == 'rls': db.exec_driver_sql('ALTER TABLE public."Discounts" ENABLE ROW LEVEL SECURITY')
    try:
        with pytest.raises(DBAPIError, match='DISCOUNT_WORKER_'):
            execute_script(owner, script)
        with owner.connect() as db:
            assert not db.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_discount_worker')"))
    finally:
        with owner.begin() as db:
            db.exec_driver_sql("UPDATE public.alembic_version SET version_num='20261115_0008'")
            if case == 'default_acl': db.exec_driver_sql('ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM PUBLIC')
            if case == 'public_routine': db.exec_driver_sql('DROP FUNCTION public.discount_worker_unsafe()')


def test_existing_role_and_nonoperator_are_rejected_without_overwriting(worker):
    engine, owner = worker
    with pytest.raises(DBAPIError, match='DISCOUNT_WORKER_EXISTS'):
        execute_script(owner, APPROVED)
    with pytest.raises(DBAPIError, match='DISCOUNT_WORKER_WRONG_TARGET_OR_OPERATOR'):
        execute_script(engine, APPROVED)
    execute_script(engine, VERIFY)


def test_null_neon_identity_cannot_bypass_destination_guard(discount_lab):
    # Simula sólo en el SQL de prueba una rama cloud sin GUCs de Neon.
    script = APPROVED.replace("(current_database() ~ '^mitbol_test_(auth_[a-f0-9]{32}|hu008_[a-f0-9]{12})$'", '(false')
    script = script.replace("current_database() = 'mitbol_cloud_dev'", 'true')
    script = script.replace("current_user = 'neondb_owner' AND session_user = 'neondb_owner'", 'true')
    script = script.replace("coalesce(current_setting('mitbol.external_tls_verified', true), '') = 'verify-full-channel-binding'", 'true')
    with pytest.raises(DBAPIError, match='DISCOUNT_WORKER_WRONG_TARGET_OR_OPERATOR'):
        execute_script(discount_lab, script)

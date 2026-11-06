"""Identidad/migración sólo en PostgreSQL temporal identificado, nunca .env."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, delete, func, inspect, insert, select, text, update
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import conftest
from app.core.passwords import hash_password
from app.core.permissions import permissions_for_roles
from app.core.session_policy import SessionPolicy, session_is_valid
from app.core.session_tokens import generate_session_token
from app.models import (
    AccountSession, AuditEvent, AuthBase, Base, Employee, EmployeeRole, Person, PersonalAccount, Role,
)


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20261106_0005"
CORE_REVISION = "20261105_0004"
PREVIOUS = "20261102_0003"
AUTH_TABLES = {"Persons", "PersonalAccounts", "Employees", "Roles", "EmployeeRoles", "AccountSessions", "AuditEvents"}
RATE_TABLES = {"AuthRateLimits", "AuthRateLimitPolicy"}


def config_for(connection=None, output=None):
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.attributes["database_url"] = "postgresql+psycopg2://offline.invalid/mitbol_unused"
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def test_metadata_is_separate_and_migration_has_one_new_head():
    assert set(AuthBase.metadata.tables) == AUTH_TABLES | RATE_TABLES
    assert len(Base.metadata.tables) == 8
    assert not set(Base.metadata.tables) & AUTH_TABLES
    scripts = ScriptDirectory.from_config(config_for())
    assert scripts.get_heads() == [REVISION]
    assert scripts.get_revision(REVISION).down_revision == CORE_REVISION
    assert scripts.get_revision(CORE_REVISION).down_revision == PREVIOUS


def test_incremental_sql_is_reviewable_and_never_connects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La generación offline intentó abrir PostgreSQL")
    monkeypatch.setattr(Engine, "connect", forbidden)
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{CORE_REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 7
    for name in AUTH_TABLES:
        assert f'CREATE TABLE "{name}"' in sql
    assert 'INSERT INTO "Roles"' in sql
    for name in Base.metadata.tables:
        assert f'ALTER TABLE "{name}"' not in sql and f'CREATE TABLE "{name}"' not in sql
    for denied in ('INSERT INTO "Persons"', 'INSERT INTO "PersonalAccounts"', 'CREATE ROLE', 'GRANT ', 'CREATE DATABASE'):
        assert denied not in sql
    assert "CREATE TABLE \"Customers\"" not in sql


def auth_target(connection):
    """Diagnóstico sin DSN/secretos: dirección observada en el servidor, no en la URL."""
    return dict(connection.execute(text("""
        SELECT current_setting('server_version_num')::int AS server_version_num,
               current_database() AS database, current_user AS current_user,
               session_user AS session_user, host(inet_server_addr()) AS server_address,
               inet_server_port() AS server_port
    """)).mappings().one())


def assert_auth_target(target):
    assert target["current_user"] == target["session_user"] == "mitbol_ci", target
    assert target["server_version_num"] // 10000 == 18, target
    # Un puerto Docker publicado como 127.0.0.1 puede llegar a otra IP en PG.
    # NULL significa socket Unix y tampoco satisface el guard SQL del escritor.
    assert target["server_address"] in {"127.0.0.1", "::1"}, target


@pytest.fixture(scope="module")
def auth_database(record_testsuite_property):
    directory_value = os.getenv("MITBOL_AUTH_TEST_CLUSTER")
    ci_url = conftest._ci_database_url()
    if directory_value:
        directory = Path(directory_value).resolve()
        assert directory.name == "data" and directory.parent.parent == Path(tempfile.gettempdir()).resolve()
        assert re.fullmatch(r"mitbol-sync-preflight-[a-f0-9]{32}", directory.parent.name)
        port = int(os.environ["MITBOL_AUTH_TEST_PORT"])
        assert 1024 <= port <= 65535
        control_url = URL.create("postgresql+psycopg2", username="mitbol_ci", host="127.0.0.1",
                                 port=port, database="template1")
    elif ci_url is not None:
        directory = None
        control_url = ci_url.set(database="postgres")
    else:
        pytest.skip("Ejecutar scripts/test_auth_phase1.py o usar el servicio temporal validado de CI")
    control = create_engine(control_url, isolation_level="AUTOCOMMIT", hide_parameters=True,
                            connect_args={"connect_timeout": 5})
    name = "mitbol_test_auth_" + uuid4().hex
    quoted = control.dialect.identifier_preparer.quote(name)
    with control.connect() as connection:
        target = auth_target(connection)
        record_testsuite_property("auth_control_target", json.dumps(target, sort_keys=True))
        assert_auth_target(target)
        if directory is not None:
            assert Path(connection.scalar(text("SHOW data_directory"))).resolve() == directory
        connection.exec_driver_sql(f"CREATE DATABASE {quoted} TEMPLATE template0 ENCODING 'UTF8'")
    engine = create_engine(control_url.set(database=name), hide_parameters=True,
                           connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000 -c lock_timeout=5000"})
    try:
        with engine.begin() as connection:
            target = auth_target(connection)
            record_testsuite_property("auth_isolated_target", json.dumps(target, sort_keys=True))
            assert_auth_target(target)
            assert target["database"] == name, target
            command.upgrade(config_for(connection), REVISION)
        record_testsuite_property("auth_isolated_database", name)
        yield engine
    finally:
        engine.dispose()
        with control.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE {quoted} WITH (FORCE)")
        control.dispose()


@pytest.fixture
def db(auth_database):
    with auth_database.connect() as connection, connection.begin() as transaction:
        sessions = sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        with sessions() as session:
            yield session
        transaction.rollback()


@pytest.fixture(scope="module")
def encoded_password():
    return hash_password("Contraseña sintética de pruebas aisladas")


def make_account(db, encoded_password, *, suffix=None):
    suffix = suffix or uuid4().hex[:12]
    account = PersonalAccount(person=Person(name="Persona sintética", phone="0000000000"),
                              email=f"  PERSONA+{suffix}@EXAMPLE.TEST  ",
                              password_hash=encoded_password, is_active=True)
    db.add(account)
    db.flush()
    return account


def make_session(account):
    now = datetime.now(timezone.utc)
    expires, idle = SessionPolicy().deadlines(now)
    token = generate_session_token()
    row = AccountSession(session_id=uuid4(), account_id=account.account_id, token_hash=token.digest,
                         auth_version=account.auth_version, created_at=now, last_activity_at=now,
                         expires_at=expires, idle_expires_at=idle)
    return row, token


def violates(db, statement):
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(statement)
        db.flush()


def test_migration_and_orm_match_including_constraints_and_indexes(db):
    connection = db.connection()
    assert MigrationContext.configure(connection).get_current_heads() == (REVISION,)
    assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | AUTH_TABLES | RATE_TABLES | {"alembic_version"}
    context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
    assert compare_metadata(context, [Base.metadata, AuthBase.metadata]) == []
    assert db.scalar(select(func.count()).select_from(Person)) == 0
    assert set(db.scalars(select(Role.code))) == {"admin", "staff"}
    inspector = inspect(connection)
    for table in AuthBase.metadata.tables.values():
        assert {item["name"] for item in inspector.get_check_constraints(table.name)} == {
            constraint.name for constraint in table.constraints if constraint.__class__.__name__ == "CheckConstraint"
        }
        assert {item["name"] for item in inspector.get_indexes(table.name) if not item.get("duplicates_constraint")} == {
            index.name for index in table.indexes
        }


def test_identity_relationships_and_database_uniqueness(db, encoded_password):
    account = make_account(db, encoded_password)
    employee = Employee(person=account.person, employee_code="staff-test", hired_at=date(2026, 1, 1), is_active=True)
    db.add(employee)
    role = db.scalar(select(Role).where(Role.code == "staff"))
    assignment = EmployeeRole(employee=employee, role=role)
    db.add(assignment)
    db.flush()
    assert employee.person_id == account.person_id
    assert account.person.employee is employee and account.person.account is account
    assert permissions_for_roles([link.role.code for link in employee.role_assignments], employee_active=employee.is_active) == {
        "catalog:read", "availability:write",
    }
    other = Person(name="Otra persona sintética", phone="0000000001")
    db.add(other)
    db.flush()
    violates(db, insert(PersonalAccount).values(person_id=account.person_id, email="another@example.test",
                                              password_hash=encoded_password, is_active=True))
    violates(db, insert(PersonalAccount).values(person_id=other.person_id, email=account.email,
                                              password_hash=encoded_password, is_active=True))
    violates(db, insert(EmployeeRole).values(person_id=employee.person_id, role_id=role.role_id))
    violates(db, insert(Employee).values(person_id=other.person_id, employee_code="staff-test",
                                       hired_at=date(2026, 1, 1), is_active=True))
    violates(db, insert(Role).values(code="staff", name="Duplicado"))


@pytest.mark.parametrize("field,value", [("email", "NOTNORMALIZED@example.test"),
                                         ("password_hash", "plaintext"), ("auth_version", -1), ("person_id", 2147483647)])
def test_account_constraints_even_when_orm_validation_is_bypassed(db, encoded_password, field, value):
    person = Person(name="Persona sintética", phone="0000000000")
    db.add(person)
    db.flush()
    payload = dict(person_id=person.person_id, email="a@example.test", password_hash=encoded_password, is_active=True)
    payload[field] = value
    violates(db, insert(PersonalAccount.__table__).values(**payload))


def test_employment_requires_real_explicit_business_data(db, encoded_password):
    account = make_account(db, encoded_password)
    violates(db, insert(Employee).values(person_id=account.person_id, employee_code="admin-test", is_active=True))
    assert db.scalar(select(func.count()).select_from(Employee)) == 0
    assert Employee.__table__.c.hired_at.default is Employee.__table__.c.hired_at.server_default is None
    assert Person.__table__.c.phone.default is Person.__table__.c.phone.server_default is None


def test_sessions_store_only_hash_and_global_version_invalidates(db, encoded_password):
    account = make_account(db, encoded_password)
    row, token = make_session(account)
    db.add(row)
    db.flush()
    assert token.value not in str(db.execute(select(AccountSession.__table__)).one())
    assert row.account is account
    now = datetime.now(timezone.utc)
    assert session_is_valid(row, account_active=True, current_auth_version=account.auth_version, now=now)
    db.execute(update(PersonalAccount).where(PersonalAccount.account_id == account.account_id).values(
        auth_version=PersonalAccount.auth_version + 1,
    ))
    db.refresh(account)
    assert not session_is_valid(row, account_active=True, current_auth_version=account.auth_version, now=now)
    violates(db, insert(AccountSession).values(**{
        column.name: getattr(row, column.name) for column in AccountSession.__table__.columns
        if column.name != "session_id"
    }, session_id=uuid4()))


@pytest.mark.parametrize("field,value", [("token_hash", "raw-session-token"), ("csrf_token_hash", "raw-csrf"),
                                         ("auth_version", -1), ("revocation_reason", "logout")])
def test_session_integrity(db, encoded_password, field, value):
    account = make_account(db, encoded_password)
    row, _ = make_session(account)
    payload = {column.name: getattr(row, column.name) for column in AccountSession.__table__.columns}
    payload[field] = value
    violates(db, insert(AccountSession).values(**payload))


def test_session_expiry_constraints_and_no_cascading_identity_deletion(db, encoded_password):
    account = make_account(db, encoded_password)
    row, _ = make_session(account)
    db.add(row)
    db.flush()
    violates(db, update(AccountSession).where(AccountSession.session_id == row.session_id).values(
        idle_expires_at=row.expires_at + timedelta(seconds=1),
    ))
    violates(db, delete(PersonalAccount).where(PersonalAccount.account_id == account.account_id))
    violates(db, delete(Person).where(Person.person_id == account.person_id))


def test_audit_nullable_actor_and_transaction_rollback(db, encoded_password):
    account = make_account(db, encoded_password)
    db.add(AuditEvent(action="auth.login", outcome="denied", failure_code="INVALID_CREDENTIAL"))
    db.flush()
    violates(db, insert(AuditEvent).values(action="auth.login", outcome="unknown"))
    event_count = db.scalar(select(func.count()).select_from(AuditEvent))
    original = account.email
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(update(PersonalAccount).where(PersonalAccount.account_id == account.account_id).values(email="changed@example.test"))
        db.add(AuditEvent(actor_account_id=account.account_id, action="auth.change_email", outcome="invalid"))
        db.flush()
    db.refresh(account)
    assert account.email == original and db.scalar(select(func.count()).select_from(AuditEvent)) == event_count


def test_concurrent_activity_update_cannot_clear_committed_revocation(auth_database, encoded_password):
    sessions = sessionmaker(bind=auth_database, expire_on_commit=False)
    with sessions.begin() as db:
        account = make_account(db, encoded_password)
        row, _ = make_session(account)
        db.add(row)
        person_id, account_id, session_id = account.person_id, account.account_id, row.session_id
    try:
        with sessions() as activity, sessions.begin() as logout:
            stale = activity.get(AccountSession, session_id)
            revoked = datetime.now(timezone.utc)
            logout.execute(update(AccountSession).where(AccountSession.session_id == session_id).values(
                revoked_at=revoked, revocation_reason="logout",
            ))
        # ORM sólo escribe campos modificados; una lectura obsoleta no limpia revoked_at.
        with activity:
            activity.add(stale)
            stale.last_activity_at = revoked
            stale.idle_expires_at = SessionPolicy().next_idle_expiry(revoked, stale.expires_at)
            activity.commit()
        with sessions() as db:
            current = db.get(AccountSession, session_id)
            assert current.revoked_at is not None
            assert not session_is_valid(current, account_active=True, current_auth_version=0, now=revoked)
    finally:
        with sessions.begin() as db:
            db.execute(delete(AccountSession).where(AccountSession.session_id == session_id))
            db.execute(delete(PersonalAccount).where(PersonalAccount.account_id == account_id))
            db.execute(delete(Person).where(Person.person_id == person_id))


def test_concurrent_auth_version_increments_do_not_lose_invalidation(auth_database, encoded_password):
    sessions = sessionmaker(bind=auth_database)
    with sessions.begin() as db:
        account = make_account(db, encoded_password)
        account_id, person_id = account.account_id, account.person_id
    barrier = Barrier(2)
    def invalidate():
        with sessions.begin() as db:
            barrier.wait(timeout=5)
            db.execute(update(PersonalAccount).where(PersonalAccount.account_id == account_id).values(
                auth_version=PersonalAccount.auth_version + 1,
            ))
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(invalidate) for _ in range(2)]
            for future in futures:
                future.result(timeout=10)
        with sessions() as db:
            assert db.get(PersonalAccount, account_id).auth_version == 2
    finally:
        with sessions.begin() as db:
            db.execute(delete(PersonalAccount).where(PersonalAccount.account_id == account_id))
            db.execute(delete(Person).where(Person.person_id == person_id))


def test_incremental_downgrade_upgrade_preserves_catalog(auth_database):
    with auth_database.begin() as connection:
        marker = connection.execute(text(
            'INSERT INTO "Products" (loyverse_id, name, synced_at) VALUES (:id, :name, now()) RETURNING product_id'
        ), {"id": "auth-migration-fixture", "name": "Catálogo sintético"}).scalar_one()
        before = connection.execute(select(Base.metadata.tables["Products"])).all()
        command.downgrade(config_for(connection), PREVIOUS)
        assert set(inspect(connection).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        assert connection.execute(select(Base.metadata.tables["Products"])).all() == before
        command.upgrade(config_for(connection), REVISION)
        assert connection.execute(select(Base.metadata.tables["Products"])).all() == before
        connection.execute(delete(Base.metadata.tables["Products"]).where(Base.metadata.tables["Products"].c.product_id == marker))

"""Operador y altas sólo con datos sintéticos en PostgreSQL temporal."""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from test_auth_persistence import auth_database
from test_auth_api import backend as auth_backend, password_hash, login, csrf_headers
from test_auth_admin_api import backend as catalog_backend
from test_admin_writer import backend as writer_backend, writer_database
from app.cli import admin_accounts as cli
from app.core.passwords import verify_password
from app.models import AccountSession, AuditEvent, Employee, EmployeeRole, Person, PersonalAccount, Role
from app.services.account_provisioning import AccountData, change_access, create_account

PASSWORD = "Contraseña sintética para aprovisionamiento 2026!"


@pytest.fixture
def sessions(auth_database):
    factory = sessionmaker(bind=auth_database, expire_on_commit=False)
    yield factory
    with factory.begin() as db:
        for model in (AuditEvent, AccountSession, EmployeeRole, Employee, PersonalAccount, Person):
            db.execute(delete(model))


def data(**changes):
    values = dict(name="Persona sintética", phone="0000000000", email=f"seed+{uuid4().hex}@example.test",
        employee_code=uuid4().hex[:20], hired_at=date(2026, 1, 1), active=True, roles=("admin",))
    values.update(changes)
    return AccountData(**values)


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_operator_creation_is_atomic_explicit_and_audited(sessions, role):
    payload = data(roles=(role,))
    with sessions.begin() as db:
        account_id, reference = create_account(db, payload, PASSWORD)
    with sessions() as db:
        account = db.get(PersonalAccount, account_id)
        assert account.password_hash.startswith("$argon2id$")
        assert verify_password(PASSWORD, account.password_hash) and account.is_active
        assert db.scalars(select(Role.code).join(EmployeeRole).where(
            EmployeeRole.person_id == account.person_id)).all() == [role]
        assert db.get(Employee, account.person_id).hired_at == payload.hired_at
        event = db.scalar(select(AuditEvent))
        assert event.action == "operator.mitbol_ci.account.create" and event.actor_account_id is None
        assert event.request_id == reference and event.resource_id == str(account_id)


@pytest.mark.parametrize("duplicate", ["email", "employee_code"])
def test_duplicates_rollback_without_orphan_person_or_audit(sessions, duplicate):
    payload = data()
    with sessions.begin() as db:
        create_account(db, payload, PASSWORD)
    other = data(**{duplicate: getattr(payload, duplicate)})
    with pytest.raises(IntegrityError), sessions.begin() as db:
        create_account(db, other, PASSWORD)
    with sessions() as db:
        for model in (Person, PersonalAccount, Employee, EmployeeRole, AuditEvent):
            assert db.scalar(select(func.count()).select_from(model)) == 1


def test_audit_failure_rolls_back_all_provisioned_rows(sessions):
    def fail(mapper, connection, target):
        raise OperationalError("synthetic", {}, Exception("synthetic"))
    event.listen(AuditEvent, "before_insert", fail)
    try:
        with pytest.raises(OperationalError), sessions.begin() as db:
            create_account(db, data(), PASSWORD)
    finally:
        event.remove(AuditEvent, "before_insert", fail)
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Person)) == 0


def test_access_change_revokes_real_session_and_keeps_audit(writer_backend):
    b = writer_backend
    response = login(b)
    with b.sessions.begin() as db:
        change_access(db, b.ids["admin"][0], account_active=True, employee_active=True, role_codes=("staff",))
    assert b.client.get("/api/auth/me").status_code == 401
    response = login(b)
    assert response.json()["user"]["roles"] == ["staff"]
    assert b.client.patch(f"/api/admin/products/{b.product_id}/description", headers=csrf_headers(response),
                          json={"description": "Forbidden"}).status_code == 403
    with b.sessions() as db:
        assert db.get(PersonalAccount, b.ids["admin"][0]).auth_version == 1
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "operator.mitbol_ci.access.change"))


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_cli_disable_preserves_creation_audit_and_revokes_access(sessions, auth_backend, auth_database,
                                                               monkeypatch, capsys, role):
    b = auth_backend
    payload = data(roles=(role,))
    with sessions.begin() as db:
        account_id, creation_reference = create_account(db, payload, PASSWORD)
    assert login(b, email=payload.email, password=PASSWORD).status_code == 200
    answers = iter([role, "no", "no"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MITBOL_PROVISION_OPERATOR", "mitbol_ci")
    monkeypatch.setenv("MITBOL_PROVISION_DATABASE_URL", auth_database.url.render_as_string(hide_password=False))
    assert cli.main(["access", str(account_id)]) == 0
    output = capsys.readouterr()
    assert "Operación confirmada" in output.out
    assert PASSWORD not in output.out + output.err and payload.email not in output.out + output.err
    assert b.client.get("/api/auth/me").status_code == 401
    assert login(b, email=payload.email, password=PASSWORD).status_code == 401
    with sessions() as db:
        account = db.get(PersonalAccount, account_id)
        assert not account.is_active and account.auth_version == 1
        assert not db.get(Employee, account.person_id).is_active
        assert db.scalars(select(Role.code).join(EmployeeRole).where(
            EmployeeRole.person_id == account.person_id)).all() == [role]
        assert db.scalar(select(AuditEvent).where(AuditEvent.request_id == creation_reference))
        assert db.scalar(select(AuditEvent).where(AuditEvent.resource_id == str(account_id),
            AuditEvent.action == "operator.mitbol_ci.access.change", AuditEvent.outcome == "success"))
        assert all(row.revoked_at is not None for row in db.scalars(
            select(AccountSession).where(AccountSession.account_id == account_id)))


def test_writer_cannot_use_operator_service(writer_database):
    with sessionmaker(bind=writer_database.writer).begin() as db:
        with pytest.raises(ValueError, match="operador"):
            create_account(db, data(), PASSWORD)


def test_cli_never_accepts_or_echoes_password_arguments(capsys):
    assert cli.main(["create", "--password", "SYNTHETIC_MUST_NOT_ECHO"]) == 2
    output = capsys.readouterr()
    assert "SYNTHETIC_MUST_NOT_ECHO" not in output.out + output.err


def test_cli_help_never_opens_operator_connection(monkeypatch, capsys):
    def forbidden():
        pytest.fail("Help attempted a database connection")
    monkeypatch.setattr(cli, "operator_transaction", forbidden)
    assert cli.main(["--help"]) == 0
    assert "Uso:" in capsys.readouterr().out


def test_bootstrap_sends_hash_not_plaintext_to_postgresql(sessions):
    observed = []
    engine = sessions.kw["bind"]
    def capture(connection, cursor, statement, parameters, context, many):
        observed.append((statement, repr(parameters)))
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with sessions.begin() as db:
            create_account(db, data(), PASSWORD)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert observed and all(PASSWORD not in sql + parameters for sql, parameters in observed)
    assert any("$argon2id$" in parameters for sql, parameters in observed)


def test_cli_creation_uses_hidden_prompt_and_confirms_only_reference(sessions, auth_database, monkeypatch, capsys):
    payload = data()
    answers = iter(["admin", payload.name, payload.phone, payload.email, payload.employee_code, "2026-01-01", "si"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli, "getpass", lambda prompt: PASSWORD)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MITBOL_PROVISION_OPERATOR", "mitbol_ci")
    monkeypatch.setenv("MITBOL_PROVISION_DATABASE_URL", auth_database.url.render_as_string(hide_password=False))
    assert cli.main(["create"]) == 0
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err and payload.email not in output.out
    with sessions() as db:
        assert db.scalar(select(PersonalAccount.password_hash)) not in output.out + output.err
    assert "referencia de auditoría" in output.out


def test_cli_rejects_hidden_prompt_fallback_before_connect(monkeypatch, capsys):
    from getpass import GetPassWarning
    import warnings
    answers = iter(["staff", "Persona sintetica", "0000000000", "seed@example.test", "SYNTHETIC", "2026-01-01", "si"])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli, "getpass", lambda prompt: warnings.warn("PRIVATE_PASSWORD_FALLBACK", GetPassWarning))
    def forbidden():
        pytest.fail("Insecure password input reached PostgreSQL")
    monkeypatch.setattr(cli, "operator_transaction", forbidden)
    assert cli.main(["create"]) == 1
    output = capsys.readouterr()
    assert "PRIVATE" not in output.out + output.err


def test_access_audit_failure_rolls_back_roles_version_and_revocation(writer_backend):
    b = writer_backend
    login(b)
    def fail(mapper, connection, target):
        if target.action.startswith("operator."):
            raise OperationalError("synthetic", {}, Exception("synthetic"))
    event.listen(AuditEvent, "before_insert", fail)
    try:
        with pytest.raises(OperationalError), b.sessions.begin() as db:
            change_access(db, b.ids["admin"][0], account_active=False, employee_active=False, role_codes=("staff",))
    finally:
        event.remove(AuditEvent, "before_insert", fail)
    assert b.client.get("/api/auth/me").json()["user"]["roles"] == ["admin"]
    with b.sessions() as db:
        assert db.get(PersonalAccount, b.ids["admin"][0]).auth_version == 0
        assert db.scalar(select(AccountSession)).revoked_at is None


def test_cli_rejects_non_interactive_password_input(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["create"]) == 2
    assert "terminal interactiva" in capsys.readouterr().err


@pytest.mark.parametrize("mode,url,operator", [
    ("local", "postgresql://mitbol_ci@localhost/mitbol_auth_local", "mitbol_ci"),
    ("test", "postgresql://mitbol_ci@localhost/mitbol_dev", "mitbol_ci"),
    ("test", "postgresql://mitbol_admin_writer@localhost/mitbol_auth_local", "mitbol_admin_writer"),
    ("cloud", "postgresql+psycopg2://mitbol_admin_writer:fake@approved.neon.tech/mitbol_cloud_dev?sslmode=verify-full&channel_binding=require", "mitbol_admin_writer"),
    ("cloud", "postgresql+psycopg2://mitbol_render_reader:fake@approved.neon.tech/mitbol_cloud_dev?sslmode=verify-full&channel_binding=require", "mitbol_render_reader"),
])
def test_cli_unsafe_operator_destinations_are_rejected_before_connect(monkeypatch, mode, url, operator):
    monkeypatch.setenv("APP_ENV", mode)
    monkeypatch.setenv("MITBOL_PROVISION_DATABASE_URL", url)
    monkeypatch.setenv("MITBOL_PROVISION_OPERATOR", operator)
    monkeypatch.setenv("EXPECTED_DATABASE", "mitbol_cloud_dev")
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", "approved.neon.tech")
    def forbidden(*args, **kwargs):
        pytest.fail("Una configuración rechazada intentó conectar")
    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    with pytest.raises(ValueError), cli.operator_transaction():
        pytest.fail("Se abrió una transacción insegura")


def test_function_warning_retains_internal_signal_and_replication_checks(writer_database):
    from test_admin_writer import denied
    r = writer_database
    with r.owner.connect() as db:
        owner_pid = db.scalar(text("SELECT pg_backend_pid()"))
        for engine in (r.reader, r.writer):
            denied(engine, f"SELECT pg_cancel_backend({owner_pid})")
            denied(engine, f"SELECT pg_terminate_backend({owner_pid},0)")
            denied(engine, "SELECT pg_drop_replication_slot('mitbol_absent_permission_probe')")


@pytest.mark.parametrize("role", ["reader", "writer"])
def test_function_warning_can_allow_own_large_objects_and_wal_without_table_grants(writer_database, role):
    """Sólo laboratorio: ejecutar estas funciones no es una prueba sugerida para Neon."""
    r = writer_database
    with getattr(r, role).connect() as db:
        # default_transaction_read_only es configurable por el cliente SQL;
        # no concede UPDATE sobre Products ni actúa como una ACL de funciones.
        db.execution_options(postgresql_readonly=False)
        try:
            oid = db.scalar(text("SELECT lo_create(0)"))
            db.execute(text("SELECT lo_put(:oid,0,decode('7878','hex'))"), {"oid": oid})
            assert bytes(db.scalar(text("SELECT lo_get(:oid)"), {"oid": oid})) == b"xx"
            assert not db.scalar(text("SELECT has_column_privilege(current_user,'public.\"ProductVariants\"','price','UPDATE')"))
            # Emisión no transaccional: el mensaje WAL sintético puede persistir
            # hasta destruir ESTE cluster aunque se revierta el objeto grande.
            assert db.scalar(text("SELECT pg_logical_emit_message(false,'mitbol_temporary_probe','synthetic',false)"))
            db.rollback()
            with r.owner.connect() as owner:
                assert not owner.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_largeobject_metadata WHERE oid=:oid)"), {"oid": oid})
        finally:
            db.rollback()
            db.invalidate()

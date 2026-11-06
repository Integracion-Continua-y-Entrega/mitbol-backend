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
from app.models import AccountSession, AuditEvent, Employee, EmployeeRole, Person, PersonalAccount
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


def test_operator_creation_is_atomic_explicit_and_audited(sessions):
    payload = data()
    with sessions.begin() as db:
        account_id, reference = create_account(db, payload, PASSWORD)
    with sessions() as db:
        account = db.get(PersonalAccount, account_id)
        assert verify_password(PASSWORD, account.password_hash) and account.is_active
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


def test_writer_cannot_use_operator_service(writer_database):
    with sessionmaker(bind=writer_database.writer).begin() as db:
        with pytest.raises(ValueError, match="operador"):
            create_account(db, data(), PASSWORD)


def test_cli_never_accepts_or_echoes_password_arguments(capsys):
    assert cli.main(["create", "--password", "SYNTHETIC_MUST_NOT_ECHO"]) == 2
    output = capsys.readouterr()
    assert "SYNTHETIC_MUST_NOT_ECHO" not in output.out + output.err


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
    assert "referencia de auditoría" in output.out


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
])
def test_cli_unsafe_operator_destinations_are_rejected_before_connect(monkeypatch, mode, url, operator):
    monkeypatch.setenv("APP_ENV", mode)
    monkeypatch.setenv("MITBOL_PROVISION_DATABASE_URL", url)
    monkeypatch.setenv("MITBOL_PROVISION_OPERATOR", operator)
    def forbidden(*args, **kwargs):
        pytest.fail("Una configuración rechazada intentó conectar")
    monkeypatch.setattr(cli, "create_database_engine", forbidden)
    with pytest.raises(ValueError), cli.operator_transaction():
        pytest.fail("Se abrió una transacción insegura")

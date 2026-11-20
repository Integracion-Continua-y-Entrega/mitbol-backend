"""HU-011: altas públicas y login reutilizado, sólo en PostgreSQL desechable."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.core.passwords import verify_password
from app.models import (
    AccountSession, AuditEvent, AuthRateLimit, AuthRateLimitPolicy, Employee, EmployeeRole, Person, PersonalAccount,
)
from app.schemas.auth import CustomerRegistrationInput
from app.services import auth_service
from app.services.persistent_auth_limiter import PostgresAttemptLimiter
from test_auth_api import backend as auth_backend, csrf_headers, login, ORIGIN, password_hash
from test_auth_persistence import auth_database


PATH = "/api/auth/register"
HEADERS = {"Origin": ORIGIN, "X-CSRF-Protection": "1"}
PASSWORD = "  Contraseña sintética de cliente HU011!  "


@pytest.fixture
def backend(auth_backend):
    b = auth_backend
    try:
        yield b
    finally:
        # La fixture de autenticación conserva y limpia sus tres cuentas originales.
        with b.sessions.begin() as db:
            original_accounts = [value[0] for value in b.ids.values()]
            original_persons = [value[1] for value in b.ids.values()]
            new_ids = select(PersonalAccount.account_id).where(PersonalAccount.account_id.not_in(original_accounts))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_account_id.in_(new_ids)))
            db.execute(delete(AccountSession).where(AccountSession.account_id.in_(new_ids)))
            db.execute(delete(PersonalAccount).where(PersonalAccount.account_id.not_in(original_accounts)))
            db.execute(delete(Person).where(Person.person_id.not_in(original_persons)))
            db.execute(delete(AuthRateLimit))
            db.execute(delete(AuthRateLimitPolicy))


def payload(**changes):
    data = {"name": "  Cliente sintético  ", "phone": "+52 (228) 123-4567",
            "email": f"cliente+{uuid4().hex}@example.test", "password": PASSWORD}
    data.update(changes)
    return data


def register(b, data, *, headers=None):
    return b.client.post(PATH, headers=HEADERS if headers is None else headers, json=data)


def counts(b):
    with b.sessions() as db:
        return tuple(db.scalar(select(func.count()).select_from(model))
                     for model in (Person, PersonalAccount, Employee, EmployeeRole, AccountSession))


def test_registration_stores_only_argon2id_and_customer_can_login_without_admin_permissions(backend, caplog):
    b, data, observed = backend, payload(), []
    data["email"] = "  " + data["email"].upper() + "  "
    engine = b.service.sessions.kw["bind"]
    def capture(connection, cursor, statement, parameters, context, many):
        observed.append(statement + repr(parameters))
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = register(b, data)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store" and "set-cookie" not in response.headers
    user = response.json()["user"]
    assert user["name"] == "Cliente sintético" and user["email"] == data["email"].strip().lower()
    assert user["roles"] == user["permissions"] == []
    with b.sessions() as db:
        account = db.get(PersonalAccount, user["account_id"])
        assert account.person_id == user["person_id"] and account.is_active and account.auth_version == 0
        assert account.email_verified_at is None and account.last_login_at is None
        assert account.person.phone == "+522281234567"
        assert account.password_hash.startswith("$argon2id$") and verify_password(PASSWORD, account.password_hash)
        assert not verify_password(PASSWORD.strip(), account.password_hash)
        assert db.get(Employee, account.person_id) is None
        assert not db.scalars(select(EmployeeRole).where(EmployeeRole.person_id == account.person_id)).all()
        assert db.scalar(select(func.count()).select_from(AccountSession)) == 0
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.register"))
        assert audit.actor_account_id == account.account_id and audit.outcome == "success"
        assert account.password_hash not in response.text + caplog.text
    assert observed and any("$argon2id$" in statement for statement in observed)
    assert all(PASSWORD not in statement for statement in observed)
    assert PASSWORD not in response.text + caplog.text and "password_hash" not in response.text
    assert b.client.get("/api/auth/me").status_code == 401
    signed_in = login(b, email=data["email"], password=PASSWORD)
    assert signed_in.status_code == 200 and signed_in.json()["user"] == user
    assert b.client.get("/api/auth/me", headers={"Origin": ORIGIN}).json()["user"] == user
    for method, path in (("GET", "/api/admin/tags"), ("PATCH", "/api/admin/products/1/description")):
        assert b.client.request(method, path, headers=csrf_headers(signed_in), json={}).status_code == 403


@pytest.mark.parametrize("existing", ["new_customer", "admin", "inactive_customer"])
def test_duplicate_normalized_email_never_changes_existing_identity_or_leaves_orphans(backend, existing):
    b, data = backend, payload()
    if existing == "new_customer":
        assert register(b, data).status_code == 201
    else:
        role = "admin" if existing == "admin" else "customer"
        data["email"] = b.emails[role]
        if existing == "inactive_customer":
            with b.sessions.begin() as db:
                db.get(PersonalAccount, b.ids[role][0]).is_active = False
    before = counts(b)
    with b.sessions() as db:
        old = db.scalar(select(PersonalAccount).where(PersonalAccount.email == data["email"]))
        identity = (old.account_id, old.person_id, old.password_hash, old.is_active, old.person.name, old.person.phone)
    data.update(email="  " + data["email"].upper() + "  ", password="Otra contraseña que no debe sustituir la existente!")
    response = register(b, data)
    assert response.status_code == 409, response.text
    assert counts(b) == before
    with b.sessions() as db:
        old = db.get(PersonalAccount, identity[0])
        assert (old.account_id, old.person_id, old.password_hash, old.is_active, old.person.name, old.person.phone) == identity


@pytest.mark.parametrize("field,value", [
    ("name", "  "), ("name", "Nombre\x00privado"), ("name", "x" * 101), ("name", 1),
    ("phone", ""), ("phone", "abc1234567"), ("phone", "123456"), ("phone", "1" * 16), ("phone", 2281234567),
    ("email", "sin-arroba"), ("email", "a b@example.test"), ("email", "a@"),
    ("password", "x" * 14), ("password", "x" * 1025), ("password", None),
    ("roles", ["admin", "staff"]), ("permissions", ["catalog:read"]), ("is_active", True),
    ("person_id", 1), ("employee", {"role": "admin"}), ("password_hash", "$argon2id$forged"),
])
def test_invalid_data_and_privilege_assignment_are_rejected_before_hash_or_write(backend, monkeypatch, caplog, field, value):
    b, data = backend, payload()
    data[field] = value
    before = counts(b)
    def forbidden(*args):
        pytest.fail("Una entrada inválida intentó generar una contraseña")
    monkeypatch.setattr(auth_service, "hash_password", forbidden)
    response = register(b, data)
    assert response.status_code == 422
    assert response.json() == {"detail": "Solicitud de autenticación inválida."}
    assert PASSWORD not in response.text + caplog.text and counts(b) == before


def test_same_name_and_phone_as_employee_never_links_customer_to_employee_or_current_session(backend):
    b = backend
    signed_in = login(b, "admin")
    with b.sessions() as db:
        admin = db.get(Person, b.ids["admin"][1])
        data = payload(name=admin.name, phone=admin.phone)
    response = register(b, data)
    assert response.status_code == 201
    assert response.json()["user"]["person_id"] != b.ids["admin"][1]
    assert response.json()["user"]["roles"] == response.json()["user"]["permissions"] == []
    assert "set-cookie" not in response.headers
    assert b.client.get("/api/auth/me").json()["user"] == signed_in.json()["user"]


@pytest.mark.parametrize("failure", ["account", "audit"])
def test_database_failure_rolls_back_person_account_and_audit_without_leaking_secrets(backend, caplog, failure):
    b, data = backend, payload()
    before = counts(b)
    def fail(mapper, connection, target):
        if failure == "account" or target.action == "auth.register":
            raise OperationalError("synthetic-private-sql", {}, Exception("synthetic-private-connection"))
    model = PersonalAccount if failure == "account" else AuditEvent
    event.listen(model, "before_insert", fail)
    try:
        response = register(b, data)
    finally:
        event.remove(model, "before_insert", fail)
    assert response.status_code == 503 and counts(b) == before
    assert "synthetic-private" not in response.text + caplog.text and PASSWORD not in response.text + caplog.text
    assert register(b, data).status_code == 201


def test_simultaneous_registration_of_same_email_commits_only_one_person_and_account(backend):
    b = backend
    data = CustomerRegistrationInput(**payload())
    before, barrier = counts(b), Barrier(2)
    def send():
        barrier.wait(timeout=5)
        try:
            b.service.register(data, ip="127.0.0.1", request_id=uuid4())
            return 201
        except AuthError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(results) == [201, 409]
    assert counts(b) == (before[0] + 1, before[1] + 1, *before[2:])


@pytest.mark.parametrize("headers", [{}, {"Origin": ORIGIN}, {"Origin": "null", "X-CSRF-Protection": "1"},
                                     {**HEADERS, "Origin": "https://evil.invalid"}])
def test_registration_reuses_origin_and_csrf_protection(backend, headers):
    before = counts(backend)
    assert register(backend, payload(), headers=headers).status_code == 403
    assert counts(backend) == before


def test_registration_reuses_json_body_limit_and_cors(backend):
    b = backend
    before = counts(b)
    preflight = b.client.options(PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-CSRF-Protection"})
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == ORIGIN
    assert b.client.post(PATH, headers=HEADERS, data=payload()).status_code == 415
    assert register(b, payload(password="x" * 20_000)).status_code == 413
    assert counts(b) == before


@pytest.mark.parametrize("persistent", [False, True])
def test_registration_rate_limits_are_reused_and_do_not_prevent_subsequent_login(backend, persistent):
    b, data = backend, payload()
    if persistent:
        config = b.config.model_copy(update={"auth_login_attempts": 1,
            "auth_limit_secret": SecretStr("synthetic-registration-rate-key-2026-abcdefgh")})
        b.service.limiter = PostgresAttemptLimiter(b.service.transaction, config)
    else:
        b.service.limiter.account_limit = 1
    assert register(b, data).status_code == 201
    denied = register(b, data)
    assert denied.status_code == 429 and int(denied.headers["retry-after"]) > 0
    assert login(b, email=data["email"], password=PASSWORD).status_code == 200


def test_registration_reuses_hash_concurrency_gate(backend):
    b = backend
    before = counts(b)
    with b.service.password_work.acquire(), b.service.password_work.acquire():
        response = register(b, payload())
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
    assert counts(b) == before

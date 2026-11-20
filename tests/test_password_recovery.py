"""HU-013: recuperación sin transporte de correo real ni conexiones cloud."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

from alembic import command
from pydantic import SecretStr
import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.core.auth_http import cookie_name
from app.core.passwords import verify_password
from app.core.session_tokens import generate_session_token, hash_session_token
from app.models import AccountSession, AuditEvent, AuthRateLimit, AuthRateLimitPolicy, Employee, PersonalAccount, PasswordResetToken
from app.services import auth_service
from app.services.persistent_auth_limiter import PostgresAttemptLimiter
from test_auth_api import backend as auth_backend, csrf_headers, login, NEW_PASSWORD, ORIGIN, PASSWORD, password_hash, token_cookie
from test_auth_persistence import auth_database, config_for


REQUEST = "/api/auth/password-recovery"
RESET = "/api/auth/reset-password"
HEADERS = {"Origin": ORIGIN, "X-CSRF-Protection": "1"}


@pytest.fixture
def backend(auth_backend):
    b = auth_backend
    with b.sessions.kw["bind"].begin() as connection:
        command.upgrade(config_for(connection), "20261119_0011")
    b.deliveries = []
    b.service.recovery_delivery = b.deliveries.append  # Captura privada de pruebas; no simula un envío real.
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(PasswordResetToken))
            db.execute(delete(AuthRateLimit))
            db.execute(delete(AuthRateLimitPolicy))


def request_recovery(b, email=None, *, headers=HEADERS):
    return b.client.post(REQUEST, headers=headers, json={"email": email or b.emails["customer"]})


def reset(b, token, *, password=NEW_PASSWORD, headers=HEADERS):
    return b.client.post(RESET, headers=headers, json={"token": token, "new_password": password})


@pytest.mark.parametrize("role", ["customer", "admin", "staff"])
def test_recovery_hashes_token_changes_password_and_revokes_every_session_without_changing_permissions(backend, role, caplog):
    b = backend
    signed_in = login(b, role)
    original_user, old_cookie = signed_in.json()["user"], token_cookie(b)
    b.service.login(b.emails[role], PASSWORD, ip="127.0.0.1", request_id=uuid4())
    account_id = b.ids[role][0]
    requested = request_recovery(b, "  " + b.emails[role].upper() + "  ")
    assert requested.status_code == 202 and set(requested.json()) == {"detail"}
    notice, = b.deliveries
    assert notice.email == b.emails[role] and notice.expires_at == b.now.value + timedelta(minutes=30)
    assert notice.token not in requested.text + repr(notice) + caplog.text
    assert "set-cookie" not in requested.headers and requested.headers["cache-control"] == "no-store"
    with b.sessions() as db:
        row = db.get(PasswordResetToken, account_id)
        assert row.token_hash == hash_session_token(notice.token) and row.token_hash != notice.token
        assert row.auth_version == db.get(PersonalAccount, account_id).auth_version == 0
        assert verify_password(PASSWORD, db.get(PersonalAccount, account_id).password_hash)
    response = reset(b, notice.token)
    assert response.status_code == 204 and not response.content
    assert response.headers["cache-control"] == "no-store" and "Max-Age=0" in response.headers["set-cookie"]
    assert notice.token not in response.text + caplog.text and NEW_PASSWORD not in caplog.text
    with b.sessions() as db:
        account = db.get(PersonalAccount, account_id)
        assert account.auth_version == 1 and account.updated_at == b.now.value
        assert account.password_hash.startswith("$argon2id$") and verify_password(NEW_PASSWORD, account.password_hash)
        assert not verify_password(PASSWORD, account.password_hash)
        assert db.get(PasswordResetToken, account_id) is None
        sessions = db.scalars(select(AccountSession).where(AccountSession.account_id == account_id)).all()
        assert len(sessions) == 2 and all(s.revoked_at is not None and s.revocation_reason == "password_reset" for s in sessions)
    b.client.cookies.set(cookie_name(b.config), old_cookie)
    assert b.client.get("/api/auth/me").status_code == 401
    b.client.cookies.clear()
    assert login(b, role).status_code == 401
    new_login = login(b, role, password=NEW_PASSWORD)
    assert new_login.status_code == 200 and new_login.json()["user"] == original_user
    permission = b.client.post("/api/_test/description", headers=csrf_headers(new_login), json={})
    assert permission.status_code == (200 if role == "admin" else 403)
    assert reset(b, notice.token).status_code == 400


@pytest.mark.parametrize("condition", ["unknown", "inactive_account", "inactive_employee"])
def test_requests_do_not_reveal_accounts_and_do_not_enable_disabled_accounts(backend, condition):
    b = backend
    email = b.emails["staff"] if condition == "inactive_employee" else b.emails["customer"]
    with b.sessions.begin() as db:
        if condition == "inactive_account":
            db.execute(update(PersonalAccount).where(PersonalAccount.account_id == b.ids["customer"][0]).values(is_active=False))
        elif condition == "inactive_employee":
            db.execute(update(Employee).where(Employee.person_id == b.ids["staff"][1]).values(is_active=False))
        else:
            email = "unknown@example.test"
    response = request_recovery(b, email)
    expected = request_recovery(b, b.emails["admin"])
    assert response.status_code == expected.status_code == 202 and response.json() == expected.json()
    assert len(b.deliveries) == 1 and b.deliveries[0].email == b.emails["admin"]
    with b.sessions() as db:
        assert db.scalar(select(func.count()).select_from(PasswordResetToken)) == 1


def test_unconfigured_delivery_does_not_return_token_or_claim_email_was_sent(backend):
    b = backend
    b.service.recovery_delivery = None
    response = request_recovery(b)
    assert response.status_code == 202 and set(response.json()) == {"detail"}
    assert "entrega está disponible" in response.json()["detail"] and b.deliveries == []
    with b.sessions() as db:
        assert db.get(PasswordResetToken, b.ids["customer"][0]) is not None


def test_delivery_occurs_after_commit_and_failure_does_not_leak_token_or_account(backend, caplog):
    b, captured = backend, []
    def fail(notice):
        with b.sessions() as db:
            assert db.get(PasswordResetToken, b.ids["customer"][0]).token_hash == hash_session_token(notice.token)
        captured.append(notice)
        raise RuntimeError("private-transport:" + notice.token)
    b.service.recovery_delivery = fail
    response = request_recovery(b)
    assert response.status_code == 202 and response.json() == request_recovery(b, "unknown@example.test").json()
    assert captured[0].token not in response.text + caplog.text and "private-transport" not in caplog.text
    with b.sessions() as db:
        assert db.scalar(select(AuditEvent.failure_code).where(AuditEvent.action == "auth.password_recovery_delivery")) == "RECOVERY_DELIVERY_FAILED"


@pytest.mark.parametrize("kind", ["unknown", "malformed", "session", "expired", "future", "rotated"])
def test_invalid_tokens_never_hash_a_new_password_or_change_account(backend, monkeypatch, kind):
    b = backend
    request_recovery(b)
    token = b.deliveries[-1].token
    if kind == "unknown":
        token = generate_session_token().value
    elif kind == "malformed":
        token = "!" * 43
    elif kind == "session":
        login(b, "customer")
        token = token_cookie(b)
    elif kind == "expired":
        b.now.value += timedelta(minutes=30)
    elif kind == "future":
        b.now.value -= timedelta(seconds=1)
    else:
        request_recovery(b)
    def forbidden(*args):
        pytest.fail("Un token inválido llegó al hash de contraseña")
    monkeypatch.setattr(auth_service, "hash_password", forbidden)
    response = reset(b, token)
    assert response.status_code == 400 and response.json() == {"detail": "El enlace de recuperación es inválido o expiró."}
    with b.sessions() as db:
        account = db.get(PersonalAccount, b.ids["customer"][0])
        assert account.auth_version == 0 and verify_password(PASSWORD, account.password_hash)


def test_request_rotates_previous_token_without_revoking_sessions_until_reset(backend):
    b = backend
    login(b, "customer")
    request_recovery(b)
    request_recovery(b)
    first, second = b.deliveries
    assert first.token != second.token
    assert b.client.get("/api/auth/me").status_code == 200
    assert reset(b, first.token).status_code == 400
    assert reset(b, second.token).status_code == 204


@pytest.mark.parametrize("condition", ["inactive_account", "inactive_employee", "password_changed", "logout_all"])
def test_access_changes_after_issuance_invalidate_recovery(backend, condition):
    b = backend
    signed_in = login(b, "staff")
    request_recovery(b, b.emails["staff"])
    if condition in {"password_changed", "logout_all"}:
        path, data = ("/change-password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}) if condition == "password_changed" else ("/logout-all", {})
        assert b.client.post("/api/auth" + path, headers=csrf_headers(signed_in), json=data).status_code == 204
    else:
        with b.sessions.begin() as db:
            if condition == "inactive_account":
                db.execute(update(PersonalAccount).where(PersonalAccount.account_id == b.ids["staff"][0]).values(is_active=False))
            else:
                db.execute(update(Employee).where(Employee.person_id == b.ids["staff"][1]).values(is_active=False))
    assert reset(b, b.deliveries[0].token).status_code == 400


@pytest.mark.parametrize("path,data", [
    (REQUEST, {"email": "sin-arroba"}), (REQUEST, {"email": None}),
    (RESET, {"token": "private", "new_password": NEW_PASSWORD}),
    (RESET, {"token": "x" * 43, "new_password": "x" * 14}),
    (RESET, {"token": "x" * 43, "new_password": "x" * 1025}),
    (RESET, {"token": "x" * 43, "new_password": 123456789012345}),
    (RESET, {"token": "x" * 43, "new_password": NEW_PASSWORD, "roles": ["admin"]}),
])
def test_invalid_contracts_are_sanitized(backend, path, data, caplog):
    response = backend.client.post(path, headers=HEADERS, json=data)
    assert response.status_code == 422 and response.json() == {"detail": "Solicitud de autenticación inválida."}
    assert NEW_PASSWORD not in response.text + caplog.text


@pytest.mark.parametrize("path", [REQUEST, RESET])
@pytest.mark.parametrize("headers", [{}, {"Origin": ORIGIN}, {**HEADERS, "Origin": "https://evil.invalid"}])
def test_recovery_preserves_origin_and_csrf_protection(backend, path, headers):
    response = backend.client.post(path, headers=headers, json={})
    assert response.status_code == 403


@pytest.mark.parametrize("path", [REQUEST, RESET])
def test_recovery_reuses_cors_json_and_body_limit(backend, path):
    b = backend
    preflight = b.client.options(path, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-CSRF-Protection"})
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == ORIGIN
    assert b.client.post(path, headers=HEADERS, content="not-json").status_code == 415
    assert b.client.post(path, headers=HEADERS, json={"private": "x" * 20_000}).status_code == 413


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("operation", ["request", "reset"])
def test_recovery_reuses_attempt_limits_and_keeps_login_independent(backend, persistent, operation):
    b = backend
    if persistent:
        config = b.config.model_copy(update={"auth_login_attempts": 1,
            "auth_limit_secret": SecretStr("synthetic-recovery-rate-key-2026-abcdefgh")})
        b.service.limiter = PostgresAttemptLimiter(b.service.transaction, config)
    else:
        b.service.limiter.account_limit = 1
    token = generate_session_token().value
    send = (lambda: request_recovery(b, "unknown@example.test")) if operation == "request" else (lambda: reset(b, token))
    assert send().status_code == (202 if operation == "request" else 400)
    denied = send()
    assert denied.status_code == 429 and int(denied.headers["retry-after"]) > 0
    assert login(b, "customer").status_code == 200


@pytest.mark.parametrize("path", [REQUEST, RESET])
def test_ip_limit_also_covers_changing_unknown_identities(backend, path):
    b = backend
    b.service.limiter.ip_limit = 1
    if path == REQUEST:
        assert request_recovery(b, "unknown1@example.test").status_code == 202
        assert request_recovery(b, "unknown2@example.test").status_code == 429
    else:
        assert reset(b, generate_session_token().value).status_code == 400
        assert reset(b, generate_session_token().value).status_code == 429


@pytest.mark.parametrize("stage", ["request", "reset"])
def test_database_failure_rolls_back_recovery_or_password_and_session_changes(backend, stage, caplog):
    b = backend
    login(b, "customer")
    if stage == "reset":
        request_recovery(b)
    def fail(mapper, connection, target):
        if target.action == ("auth.password_recovery" if stage == "request" else "auth.reset_password"):
            raise OperationalError("private-sql", {}, Exception("private-connection"))
    event.listen(AuditEvent, "before_insert", fail)
    try:
        response = request_recovery(b) if stage == "request" else reset(b, b.deliveries[0].token)
    finally:
        event.remove(AuditEvent, "before_insert", fail)
    assert response.status_code == 503 and "private-" not in response.text + caplog.text
    with b.sessions() as db:
        account = db.get(PersonalAccount, b.ids["customer"][0])
        assert account.auth_version == 0 and verify_password(PASSWORD, account.password_hash)
        assert db.scalar(select(AccountSession.revoked_at)) is None
        assert (db.get(PasswordResetToken, account.account_id) is not None) == (stage == "reset")
    if stage == "request":
        assert b.deliveries == []
        assert request_recovery(b).status_code == 202
    assert reset(b, b.deliveries[0].token).status_code == 204


def test_concurrent_consumption_commits_once(backend):
    b, barrier = backend, Barrier(2)
    request_recovery(b)
    token = b.deliveries[0].token
    def consume():
        barrier.wait(timeout=5)
        try:
            b.service.reset_password(token, NEW_PASSWORD, ip="127.0.0.1", request_id=uuid4())
            return 204
        except AuthError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [204, 400]
    with b.sessions() as db:
        assert db.get(PersonalAccount, b.ids["customer"][0]).auth_version == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "auth.reset_password")) == 1


def test_hash_gate_failure_preserves_token_for_retry(backend):
    b = backend
    request_recovery(b)
    token = b.deliveries[0].token
    with b.service.password_work.acquire(), b.service.password_work.acquire():
        assert reset(b, token).status_code == 429
    assert reset(b, token).status_code == 204

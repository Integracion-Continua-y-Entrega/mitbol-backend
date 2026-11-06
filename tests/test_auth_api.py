"""Autenticación HTTP exclusivamente en la base PostgreSQL temporal validada."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Depends, Request
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, event, func, insert, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from test_auth_persistence import auth_database
from app.api.auth_dependencies import get_current_user, require_permission
from app.core.auth_http import cookie_name
from app.core.auth_errors import AuthError
from app.core.config import Settings
from app.core.passwords import hash_password, verify_password
from app.db.database import get_db
from app.main import create_app
from app.models import AccountSession, AuditEvent, Employee, EmployeeRole, Person, PersonalAccount, Role
from app.repositories import auth as repo


ORIGIN = "http://localhost:5173"
PASSWORD = "Contraseña sintética larga para HTTP 2026!"
NEW_PASSWORD = "Nueva contraseña sintética larga para HTTP 2026!"


@pytest.fixture(scope="module")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def backend(auth_database, password_hash):
    sessions = sessionmaker(bind=auth_database, expire_on_commit=False)
    suffix = uuid4().hex[:10]
    ids, emails = {}, {}
    with sessions.begin() as db:
        for code in ("admin", "staff"):
            person = Person(name=f"Persona HTTP sintética {code}", phone="0000000000")
            account = PersonalAccount(person=person, email=f"{code}+{suffix}@example.test", password_hash=password_hash, is_active=True)
            employee = Employee(person=person, employee_code=f"{code}-{suffix}", hired_at=date(2026, 1, 1), is_active=True)
            db.add_all([account, employee])
            db.flush()
            role_id = db.scalar(select(Role.role_id).where(Role.code == code))
            db.add(EmployeeRole(person_id=person.person_id, role_id=role_id))
            ids[code], emails[code] = (account.account_id, person.person_id), account.email
    config = Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
        admin_enabled=False, auth_enabled=True, auth_catalog_enabled=True, auth_images_enabled=True,
        auth_database_url=auth_database.url.render_as_string(hide_password=False),
        auth_cookie_secure=False, auth_login_attempts=50, auth_ip_attempts=200)
    application = create_app(config)
    application.dependency_overrides[get_db] = lambda: SimpleNamespace(scalars=lambda _: SimpleNamespace(all=lambda: []))

    @application.post("/api/_test/description")
    def description(user=Depends(require_permission("description:write"))):
        return {"actor": user.account_id}

    @application.post("/api/_test/availability")
    def availability(user=Depends(require_permission("availability:write"))):
        return {"actor": user.account_id}

    @application.get("/api/_test/current")
    def current(user=Depends(get_current_user, scope="function")):
        return {"actor": user.account_id}

    now = SimpleNamespace(value=datetime.now(timezone.utc))
    application.state.auth_service.clock = lambda: now.value
    with TestClient(application, base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        yield SimpleNamespace(client=client, app=application, service=application.state.auth_service,
                              sessions=sessions, config=config, ids=ids, emails=emails, now=now)
    with sessions.begin() as db:
        # Esta base es exclusiva de la fixture; también limpia auditoría anónima.
        db.execute(delete(AuditEvent))
        db.execute(delete(AccountSession))
        for account_id, person_id in ids.values():
            db.execute(delete(EmployeeRole).where(EmployeeRole.person_id == person_id))
            db.execute(delete(Employee).where(Employee.person_id == person_id))
            db.execute(delete(PersonalAccount).where(PersonalAccount.account_id == account_id))
            db.execute(delete(Person).where(Person.person_id == person_id))


def login(backend, role="admin", *, client=None, password=PASSWORD, email=None):
    return (client or backend.client).post("/api/auth/login", headers={"Origin": ORIGIN, "X-CSRF-Protection": "1"},
        json={"email": email or backend.emails[role], "password": password})


def csrf_headers(response):
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def token_cookie(backend, client=None):
    return (client or backend.client).cookies.get(cookie_name(backend.config))


def test_login_persists_only_hashes_and_cookie_attributes_and_me(backend):
    response = login(backend)
    assert response.status_code == 200, response.text
    payload = response.json()
    token = token_cookie(backend)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert "Secure" not in cookie and "Domain=" not in cookie
    assert "Max-Age=28800" in cookie and response.headers["cache-control"] == "no-store"
    assert token not in response.text and "password_hash" not in response.text and "session_id" not in response.text
    assert payload["user"]["roles"] == ["admin"]
    with backend.sessions() as db:
        row = db.scalar(select(AccountSession))
        assert len(row.token_hash) == 64 and row.token_hash != token
        assert row.csrf_token_hash != payload["csrf_token"]
        assert db.get(PersonalAccount, backend.ids["admin"][0]).last_login_at is not None
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.outcome == "success")) == 1
    me = backend.client.get("/api/auth/me", headers={"Origin": ORIGIN})
    assert me.status_code == 200 and me.json()["user"] == payload["user"]
    assert backend.client.get("/api/auth/csrf").json() == {"csrf_token": payload["csrf_token"]}


@pytest.mark.parametrize("condition", ["wrong_password", "missing", "inactive", "employee_disabled", "no_employee"])
def test_login_denials_are_identical_and_create_no_session(backend, condition):
    if condition in {"inactive", "employee_disabled", "no_employee"}:
        with backend.sessions.begin() as db:
            if condition == "inactive":
                db.execute(update(PersonalAccount).where(PersonalAccount.account_id == backend.ids["admin"][0]).values(is_active=False))
            elif condition == "employee_disabled":
                db.execute(update(Employee).where(Employee.person_id == backend.ids["admin"][1]).values(is_active=False))
            else:
                db.execute(delete(EmployeeRole).where(EmployeeRole.person_id == backend.ids["admin"][1]))
                db.execute(delete(Employee).where(Employee.person_id == backend.ids["admin"][1]))
    response = login(backend, password="incorrecta" if condition == "wrong_password" else PASSWORD,
                     email="unknown@example.test" if condition == "missing" else None)
    assert response.status_code == 401 and response.json() == {"detail": "Credenciales inválidas."}
    with backend.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AccountSession)) == 0
        event = db.scalar(select(AuditEvent))
        assert event.outcome == "denied" and event.actor_account_id is None


def test_dummy_hash_is_used_for_unknown_account(backend, monkeypatch):
    from app.services import auth_service
    seen = []
    original = auth_service.verify_password
    def verify(password, encoded):
        seen.append(encoded)
        return original(password, encoded)
    monkeypatch.setattr(auth_service, "verify_password", verify)
    assert login(backend, email="missing@example.test").status_code == 401
    assert seen == [backend.service._dummy_hash]


@pytest.mark.parametrize("role,path,status", [("admin", "description", 200), ("staff", "description", 403),
                                               ("admin", "availability", 200), ("staff", "availability", 200)])
def test_permission_dependencies_on_isolated_routes(backend, role, path, status):
    response = login(backend, role)
    assert response.status_code == 200
    result = backend.client.post(f"/api/_test/{path}", headers=csrf_headers(response), json={})
    assert result.status_code == status, result.text
    assert backend.client.get("/api/_test/current").status_code == 200


def test_real_admin_routes_require_permissions_and_public_menu_never_requires_cookie(backend):
    assert backend.client.get("/api/menu").status_code == 200
    assert backend.client.post("/api/_test/description", headers={"Origin": ORIGIN}, json={}).status_code == 401
    response = login(backend)
    schema = backend.client.get("/openapi.json").json()
    assert "/api/admin/tags" in schema["paths"]
    assert backend.client.get("/api/admin/tags").status_code == 200
    response = login(backend, "staff")
    assert backend.client.patch("/api/admin/products/1/description",
        headers=csrf_headers(response), json={"description": "Denied"}).status_code == 403
    backend.now.value += timedelta(minutes=31)
    assert backend.client.get("/api/menu").status_code == 200
    assert backend.client.get("/api/auth/me").status_code == 401


def test_activity_renewal_is_controlled_and_never_extends_absolute(backend):
    login(backend)
    with backend.sessions() as db:
        first = db.scalar(select(AccountSession))
        initial, absolute = first.idle_expires_at, first.expires_at
    backend.now.value += timedelta(seconds=30)
    assert backend.client.get("/api/auth/me").status_code == 200
    with backend.sessions() as db:
        assert db.scalar(select(AccountSession)).idle_expires_at == initial
    # Actividad cada 29 minutos mantiene la sesión hasta su límite absoluto.
    for _ in range(16):
        backend.now.value += timedelta(minutes=29)
        response = backend.client.get("/api/auth/me")
        assert response.status_code == 200
        assert datetime.fromisoformat(response.json()["idle_expires_at"]) <= absolute
    backend.now.value = absolute
    assert backend.client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("path", ["logout", "logout-all", "change-password"])
def test_post_operations_require_origin_and_csrf(backend, path):
    response = login(backend)
    payload = {"current_password": PASSWORD, "new_password": NEW_PASSWORD} if path == "change-password" else {}
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
                    {"Origin": "null", "X-CSRF-Token": response.json()["csrf_token"]},
                    {"Origin": "https://evil.example", "X-CSRF-Token": response.json()["csrf_token"]}):
        assert backend.client.post(f"/api/auth/{path}", headers=headers, json=payload).status_code == 403
    assert backend.client.get("/api/auth/me").status_code == 200


def test_logout_revokes_and_replay_fails(backend):
    response = login(backend)
    token = token_cookie(backend)
    result = backend.client.post("/api/auth/logout", headers=csrf_headers(response), json={})
    assert result.status_code == 204 and "Max-Age=0" in result.headers["set-cookie"]
    backend.client.cookies.set(cookie_name(backend.config), token)
    assert backend.client.get("/api/auth/me").status_code == 401
    with backend.sessions() as db:
        assert db.scalar(select(AccountSession)).revocation_reason == "logout"


def test_logout_all_revokes_other_devices(backend):
    response = login(backend)
    with TestClient(backend.app, base_url="http://localhost:8000", client=("127.0.0.1", 54322)) as other:
        assert login(backend, client=other).status_code == 200
        assert backend.client.post("/api/auth/logout-all", headers=csrf_headers(response), json={}).status_code == 204
        assert other.get("/api/auth/me").status_code == 401
    with backend.sessions() as db:
        assert db.get(PersonalAccount, backend.ids["admin"][0]).auth_version == 1
        assert all(row.revoked_at is not None for row in db.scalars(select(AccountSession)))


def test_password_change_invalidates_all_sessions_and_old_password(backend):
    response = login(backend)
    old_token = token_cookie(backend)
    result = backend.client.post("/api/auth/change-password", headers=csrf_headers(response),
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert result.status_code == 204
    backend.client.cookies.set(cookie_name(backend.config), old_token)
    assert backend.client.get("/api/auth/me").status_code == 401
    assert login(backend).status_code == 401
    assert login(backend, password=NEW_PASSWORD).status_code == 200
    with backend.sessions() as db:
        account = db.get(PersonalAccount, backend.ids["admin"][0])
        assert account.auth_version == 1 and verify_password(NEW_PASSWORD, account.password_hash)


def test_wrong_current_password_does_not_change_hash_or_invalidate_session(backend):
    response = login(backend)
    with backend.sessions() as db:
        before = db.get(PersonalAccount, backend.ids["admin"][0]).password_hash
    result = backend.client.post("/api/auth/change-password", headers=csrf_headers(response),
        json={"current_password": "incorrecta", "new_password": NEW_PASSWORD})
    assert result.status_code == 400
    assert backend.client.get("/api/auth/me").status_code == 200
    with backend.sessions() as db:
        assert db.get(PersonalAccount, backend.ids["admin"][0]).password_hash == before


@pytest.mark.parametrize("change", ["auth_version", "inactive", "employee_disabled", "roles", "hash"])
def test_observed_security_changes_invalidate_existing_session(backend, change):
    response = login(backend)
    with backend.sessions.begin() as db:
        if change == "auth_version":
            db.execute(update(PersonalAccount).where(PersonalAccount.account_id == backend.ids["admin"][0]).values(auth_version=1))
        elif change == "inactive":
            db.execute(update(PersonalAccount).where(PersonalAccount.account_id == backend.ids["admin"][0]).values(is_active=False))
        elif change == "employee_disabled":
            db.execute(update(Employee).where(Employee.person_id == backend.ids["admin"][1]).values(is_active=False))
        elif change == "hash":
            db.execute(update(PersonalAccount).where(PersonalAccount.account_id == backend.ids["admin"][0]).values(password_hash=hash_password(NEW_PASSWORD)))
        else:
            db.execute(delete(EmployeeRole).where(EmployeeRole.person_id == backend.ids["admin"][1]))
    assert backend.client.get("/api/auth/me").status_code == 401
    with backend.sessions() as db:
        assert db.scalar(select(AccountSession)).revoked_at is not None


def test_login_rotates_old_session_and_csrf_from_another_session_is_rejected(backend):
    first = login(backend)
    old = token_cookie(backend)
    second = login(backend)
    assert token_cookie(backend) != old and first.json()["csrf_token"] != second.json()["csrf_token"]
    assert backend.client.post("/api/_test/availability", headers=csrf_headers(first), json={}).status_code == 403
    assert backend.client.post("/api/_test/availability", headers=csrf_headers(second), json={}).status_code == 200


def test_cors_preflight_and_untrusted_origin(backend):
    response = backend.client.options("/api/auth/login", headers={"Origin": ORIGIN,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-csrf-protection"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    denied = backend.client.options("/api/auth/login", headers={"Origin": "https://evil.example",
                                                              "Access-Control-Request-Method": "POST"})
    assert denied.status_code == 400 and "access-control-allow-origin" not in denied.headers
    assert backend.client.get("/api/auth/csrf", headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize("headers", [{}, {"Origin": "null", "X-CSRF-Protection": "1"},
                                     {"Origin": ORIGIN}, {"Origin": "https://evil.example", "X-CSRF-Protection": "1"}])
def test_login_csrf_requires_explicit_origin_and_custom_header(backend, headers):
    result = backend.client.post("/api/auth/login", headers=headers, json={"email": backend.emails["admin"], "password": PASSWORD})
    assert result.status_code == 403


def test_form_login_and_oversized_json_are_rejected_before_parsing(backend):
    headers = {"Origin": ORIGIN, "X-CSRF-Protection": "1"}
    assert backend.client.post("/api/auth/login", headers=headers, data={"email": "x", "password": "x"}).status_code == 415
    assert backend.client.post("/api/auth/login", headers=headers, json={"password": "s" * 20_000}).status_code == 413


@pytest.mark.parametrize("extra", [{"X-Forwarded-For": "127.0.0.1"}, {"Forwarded": "for=127.0.0.1"}, {"Host": "evil.example"}])
def test_proxy_and_remote_requests_rejected(backend, extra):
    headers = {"Origin": ORIGIN, "X-CSRF-Protection": "1", **extra}
    assert backend.client.post("/api/auth/login", headers=headers, json={"email": backend.emails["admin"], "password": PASSWORD}).status_code == 403
    with TestClient(backend.app, base_url="http://localhost:8000", client=("203.0.113.2", 54323)) as remote:
        assert login(backend, client=remote).status_code == 403


def test_validation_and_database_failures_never_echo_passwords_or_dsn(backend, monkeypatch, caplog):
    secret = "synthetic-password-must-not-be-echoed"
    result = backend.client.post("/api/auth/login", headers={"Origin": ORIGIN, "X-CSRF-Protection": "1"},
        json={"email": [], "password": secret})
    assert result.status_code == 422 and secret not in result.text + caplog.text
    def failure(*args, **kwargs):
        raise OperationalError("SQL synthetic password=must-not-leak", {}, Exception("private database details"))
    monkeypatch.setattr(repo, "account_for_email", failure)
    result = login(backend)
    assert result.status_code == 503
    assert "must-not-leak" not in result.text + caplog.text and "private database details" not in result.text + caplog.text


def test_login_rate_limit_and_retry_after(backend):
    backend.service.limiter.account_limit = 2
    assert login(backend, password="bad").status_code == 401
    assert login(backend, password="bad").status_code == 401
    result = login(backend)
    assert result.status_code == 429 and int(result.headers["retry-after"]) > 0


def test_audit_failure_rolls_back_login_and_password_change(backend):
    def fail_audit(mapper, connection, target):
        if target.action in {"auth.login", "auth.change_password"} and target.outcome == "success":
            raise OperationalError("synthetic", {}, Exception("synthetic"))
    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        assert login(backend).status_code == 503
        with backend.sessions() as db:
            assert db.scalar(select(func.count()).select_from(AccountSession)) == 0
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)
    response = login(backend)
    with backend.sessions() as db:
        before = db.get(PersonalAccount, backend.ids["admin"][0]).password_hash
    event.listen(AuditEvent, "before_insert", fail_audit)
    try:
        result = backend.client.post("/api/auth/change-password", headers=csrf_headers(response),
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        assert result.status_code == 503
    finally:
        event.remove(AuditEvent, "before_insert", fail_audit)
    with backend.sessions() as db:
        account = db.get(PersonalAccount, backend.ids["admin"][0])
        assert account.password_hash == before and account.auth_version == 0
        assert db.scalar(select(AccountSession)).revoked_at is None


def test_concurrent_logout_and_activity_do_not_resurrect_session(backend):
    response = login(backend)
    token = token_cookie(backend)
    principal_id = backend.ids["admin"][0]
    started, release = Event(), Event()
    def access():
        with backend.service.authenticated(token, request_id=uuid4()) as context:
            assert context.principal.account_id == principal_id
            started.set()
            assert release.wait(5)
    def logout():
        with backend.service.authenticated(token, request_id=uuid4(), csrf_header=response.json()["csrf_token"], require_csrf=True) as context:
            backend.service.logout(context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(access)
        assert started.wait(5)
        second = pool.submit(logout)
        release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert backend.client.get("/api/auth/me").status_code == 401


def test_csrf_is_stable_across_service_restart(backend):
    from app.services.auth_service import AuthService
    response = login(backend)
    new_service = AuthService(backend.app.state.auth_session_factory, backend.config, clock=backend.service.clock)
    backend.app.state.auth_service = new_service
    result = backend.client.get("/api/auth/csrf")
    assert result.status_code == 200 and result.json()["csrf_token"] == response.json()["csrf_token"]


def test_https_profile_uses_secure_host_cookie_and_http_is_rejected(backend):
    backend.config.auth_cookie_secure = True
    assert login(backend).status_code == 400
    with TestClient(backend.app, base_url="https://localhost:8000", client=("127.0.0.1", 54324)) as https:
        response = login(backend, client=https)
        assert response.status_code == 200
        assert "__Host-mitbol_session=" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]


def test_missing_or_malformed_cookie_rejected_without_database_and_alternate_headers_ignored(backend, monkeypatch):
    def forbidden():
        raise AssertionError("Una cookie ausente o mal formada no debe consultar PostgreSQL")
    monkeypatch.setattr(backend.service, "sessions", forbidden)
    for headers in ({}, {"Authorization": "Bearer synthetic", "X-Admin-Key": "synthetic"},
                    {"Cookie": f"{cookie_name(backend.config)}=malformed"}):
        result = backend.client.get("/api/auth/me?session=synthetic", headers=headers)
        assert result.status_code == 401


def test_email_case_and_spacing_share_the_same_limit(backend):
    backend.service.limiter.account_limit = 1
    assert login(backend, email="  " + backend.emails["admin"].upper() + "  ").status_code == 200
    assert login(backend).status_code == 429


@pytest.mark.parametrize("path", ["logout", "logout-all"])
def test_logout_audit_failure_returns_error_before_response_and_rolls_back(backend, path):
    response = login(backend)
    def fail(mapper, connection, target):
        if target.action in {"auth.logout", "auth.logout_all"}:
            raise OperationalError("synthetic", {}, Exception("synthetic"))
    event.listen(AuditEvent, "before_insert", fail)
    try:
        result = backend.client.post(f"/api/auth/{path}", headers=csrf_headers(response), json={})
        assert result.status_code == 503 and "set-cookie" not in result.headers
    finally:
        event.remove(AuditEvent, "before_insert", fail)
    assert backend.client.get("/api/auth/me").status_code == 200
    with backend.sessions() as db:
        assert db.scalar(select(AccountSession)).revoked_at is None
        assert db.get(PersonalAccount, backend.ids["admin"][0]).auth_version == 0


def test_dependency_transaction_rolls_back_route_changes_before_permission_error(backend):
    @backend.app.post("/api/_test/rollback")
    def protected(request: Request, user=Depends(require_permission("availability:write"))):
        request.state.auth_context.db.execute(update(Person).where(Person.person_id == user.person_id).values(name="Must roll back"))
        raise AuthError(403, "Operación ficticia denegada.", code="SYNTHETIC_DENIAL")
    response = login(backend, "staff")
    result = backend.client.post("/api/_test/rollback", headers=csrf_headers(response), json={})
    assert result.status_code == 403
    with backend.sessions() as db:
        assert db.get(Person, backend.ids["staff"][1]).name != "Must roll back"


def test_password_change_revokes_a_second_device(backend):
    response = login(backend)
    with TestClient(backend.app, base_url="http://localhost:8000", client=("127.0.0.1", 54325)) as other:
        assert login(backend, client=other).status_code == 200
        assert backend.client.post("/api/auth/change-password", headers=csrf_headers(response),
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 204
        assert other.get("/api/auth/me").status_code == 401


def test_login_racing_with_security_change_cannot_issue_a_session_that_survives_invalidation(backend, monkeypatch):
    from app.services import auth_service
    verifying, release, changing = Event(), Event(), Event()
    original = auth_service.verify_password
    replacement_hash = hash_password(NEW_PASSWORD)
    def verify(password, encoded):
        verifying.set()
        assert release.wait(5)
        return original(password, encoded)
    monkeypatch.setattr(auth_service, "verify_password", verify)
    def issue():
        return backend.service.login(backend.emails["admin"], PASSWORD, ip="127.0.0.1", request_id=uuid4())
    def change():
        with backend.sessions.begin() as db:
            changing.set()
            account = repo.lock_account(db, backend.ids["admin"][0])
            account.password_hash = replacement_hash
            repo.invalidate_locked_account(db, account, backend.now.value, "synthetic_security_change")
    with ThreadPoolExecutor(max_workers=2) as pool:
        issued = pool.submit(issue)
        assert verifying.wait(5)
        changed = pool.submit(change)
        assert changing.wait(5)
        release.set()
        result = issued.result(timeout=10)
        changed.result(timeout=10)
    with pytest.raises(AuthError) as error:
        with backend.service.authenticated(result[2].value, request_id=uuid4()):
            pytest.fail("La sesión concurrente debe quedar invalidada")
    assert error.value.status_code == 401

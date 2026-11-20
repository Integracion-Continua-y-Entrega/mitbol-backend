"""HU-015: perfil propio y persistencia en PostgreSQL desechable."""

from datetime import timedelta

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from app.core.auth_http import cookie_name
from app.models import AccountSession, AuditEvent, Person, PersonalAccount
from test_auth_api import backend as auth_backend, csrf_headers, login, ORIGIN, password_hash, token_cookie
from test_auth_persistence import auth_database
from test_customer_registration import backend, payload, register, PASSWORD


PATH = "/api/auth/profile"
PROFILE_FIELDS = {"account_id", "person_id", "email", "name", "phone"}


@pytest.mark.parametrize("role", ["customer", "admin", "staff"])
def test_own_profile_update_persists_without_changing_credentials_session_permissions_or_me_contract(backend, role):
    b = backend
    signed_in = login(b, role)
    original_user = signed_in.json()["user"]
    account_id, person_id = b.ids[role]
    with b.sessions() as db:
        account = db.get(PersonalAccount, account_id)
        original_security = (account.email, account.password_hash, account.auth_version, account.is_active, account.updated_at)
    before = b.client.get(PATH)
    assert before.status_code == 200 and before.headers["cache-control"] == "no-store"
    profile = before.json()
    assert set(profile) == PROFILE_FIELDS
    assert profile == {"account_id": account_id, "person_id": person_id, "email": b.emails[role],
                       "name": original_user["name"], "phone": "0000000000"}
    response = b.client.patch(PATH, headers=csrf_headers(signed_in),
                              json={"name": "  María López  ", "phone": " +52 (228) 123-4567 "})
    expected = profile | {"name": "María López", "phone": "+522281234567"}
    assert response.status_code == 200 and response.json() == expected
    assert response.headers["cache-control"] == "no-store" and "set-cookie" not in response.headers
    with b.sessions() as db:
        person = db.get(Person, person_id)
        assert (person.name, person.phone, person.updated_at) == (expected["name"], expected["phone"], b.now.value)
        account = db.get(PersonalAccount, account_id)
        assert (account.email, account.password_hash, account.auth_version, account.is_active, account.updated_at) == original_security
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.update_profile"))
        assert audit.actor_account_id == account_id and audit.outcome == "success"
        for other_role, (_, other_person_id) in b.ids.items():
            if other_role != role:
                other = db.get(Person, other_person_id)
                assert other.name == f"Persona HTTP sintética {other_role}" and other.updated_at is None
    assert b.client.get(PATH).json() == expected
    me = b.client.get("/api/auth/me").json()
    assert set(me) == {"user", "expires_at", "idle_expires_at"}
    assert me["user"] == original_user | {"name": "María López"}
    assert b.client.get("/api/auth/csrf").json()["csrf_token"] == signed_in.json()["csrf_token"]
    permitted = b.client.post("/api/_test/description", headers=csrf_headers(signed_in), json={})
    assert permitted.status_code == (200 if role == "admin" else 403)
    assert login(b, role).json()["user"] == original_user | {"name": "María López"}


@pytest.mark.parametrize("changes", [{"name": "Alias actualizado"}, {"phone": "2281234567"}])
def test_partial_update_preserves_omitted_personal_fields(backend, changes):
    b = backend
    signed_in = login(b, "customer")
    before = b.client.get(PATH).json()
    response = b.client.patch(PATH, headers=csrf_headers(signed_in), json=changes)
    assert response.status_code == 200 and response.json() == before | changes
    assert b.client.get(PATH).json() == before | changes


def test_newly_registered_customer_can_manage_profile_and_login_again(backend):
    b, data = backend, payload()
    registered = register(b, data)
    assert registered.status_code == 201
    signed_in = login(b, email=data["email"], password=PASSWORD)
    assert signed_in.status_code == 200
    response = b.client.patch(PATH, headers=csrf_headers(signed_in), json={"name": "Cliente actualizado"})
    assert response.status_code == 200
    profile = response.json()
    assert profile["account_id"] == registered.json()["user"]["account_id"]
    assert profile["phone"] == "+522281234567" and profile["email"] == data["email"]
    again = login(b, email=data["email"], password=PASSWORD)
    assert again.status_code == 200
    assert again.json()["user"]["name"] == "Cliente actualizado"
    assert again.json()["user"]["roles"] == again.json()["user"]["permissions"] == []


@pytest.mark.parametrize("changes", [
    {}, {"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "A\nB"},
    {"name": "A\u200bB"}, {"name": 123}, {"name": None}, {"phone": None},
    {"phone": ""}, {"phone": "123456"}, {"phone": "1" * 16},
    {"phone": "invalid"}, {"phone": 2281234567},
])
def test_invalid_personal_data_is_rejected_without_partial_writes(backend, changes):
    b = backend
    signed_in = login(b, "customer")
    before = b.client.get(PATH).json()
    response = b.client.patch(PATH, headers=csrf_headers(signed_in), json=changes)
    assert response.status_code == 422 and response.json() == {"detail": "Solicitud de autenticación inválida."}
    assert response.headers["cache-control"] == "no-store" and b.client.get(PATH).json() == before
    with b.sessions() as db:
        assert db.get(Person, b.ids["customer"][1]).updated_at is None


@pytest.mark.parametrize("field,value", [
    ("email", " ADMIN@example.test "), ("password", "Contraseña no autorizada para el perfil"),
    ("password_hash", "$argon2id$forged"), ("roles", ["admin"]), ("permissions", ["catalog:write"]),
    ("is_active", True), ("auth_version", 123), ("employee_code", "ADMIN"),
    ("account_id", None), ("person_id", None), ("updated_at", "2026-11-19T12:00:00Z"),
])
def test_only_allowlisted_fields_are_accepted_and_valid_changes_are_not_partially_applied(backend, field, value):
    b = backend
    signed_in = login(b, "customer")
    before = b.client.get(PATH).json()
    if field in {"account_id", "person_id"}:
        value = b.ids["admin"][0 if field == "account_id" else 1]
    response = b.client.patch(PATH, headers=csrf_headers(signed_in), json={"name": "No guardar", field: value})
    assert response.status_code == 422
    assert b.client.get(PATH).json() == before
    assert b.client.get("/api/auth/me").json()["user"]["permissions"] == []


def test_session_alone_selects_profile_and_other_account_ids_cannot_address_profiles(backend):
    b = backend
    signed_in = login(b, "customer")
    foreign_id = b.ids["admin"][0]
    response = b.client.get(PATH, params={"account_id": foreign_id, "person_id": b.ids["admin"][1]})
    assert response.status_code == 200 and response.json()["account_id"] == b.ids["customer"][0]
    assert b.client.get(f"{PATH}/{foreign_id}").status_code == 404
    assert b.client.patch(f"{PATH}/{foreign_id}", headers=csrf_headers(signed_in), json={"name": "Ajeno"}).status_code == 404
    with b.sessions() as db:
        assert db.get(Person, b.ids["admin"][1]).name == "Persona HTTP sintética admin"


@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_profile_requires_authentication(backend, method):
    response = backend.client.request(method, PATH, headers={"Origin": ORIGIN},
                                      **({"json": {"name": "Sin sesión"}} if method == "PATCH" else {}))
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("condition", ["expired", "revoked", "inactive"])
def test_profile_rejects_invalid_security_state_for_read_and_write(backend, condition):
    b = backend
    signed_in = login(b, "customer")
    cookie = token_cookie(b)
    if condition == "expired":
        b.now.value += timedelta(hours=9)
    else:
        with b.sessions.begin() as db:
            if condition == "revoked":
                db.execute(update(AccountSession).values(revoked_at=b.now.value, revocation_reason="synthetic_revocation"))
            else:
                db.execute(update(PersonalAccount).where(PersonalAccount.account_id == b.ids["customer"][0]).values(is_active=False))
    headers = csrf_headers(signed_in) | {"Cookie": f"{cookie_name(b.config)}={cookie}"}
    assert b.client.get(PATH, headers=headers).status_code == 401
    assert b.client.patch(PATH, headers=headers, json={"name": "No guardar"}).status_code == 401
    with b.sessions() as db:
        person = db.get(Person, b.ids["customer"][1])
        assert person.name == "Persona HTTP sintética customer" and person.updated_at is None


def test_profile_updates_require_valid_origin_and_session_csrf(backend):
    b = backend
    signed_in = login(b, "customer")
    for headers in ({}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "0" * 64},
                    {**csrf_headers(signed_in), "Origin": "https://evil.invalid"},
                    {"X-CSRF-Token": signed_in.json()["csrf_token"]}):
        assert b.client.patch(PATH, headers=headers, json={"name": "No guardar"}).status_code == 403
    assert b.client.get(PATH).json()["name"] == "Persona HTTP sintética customer"
    assert b.client.patch(PATH, headers=csrf_headers(signed_in), json={"name": "Cambio autorizado"}).status_code == 200


def test_profile_reuses_json_body_limit_and_credentialed_cors(backend):
    b = backend
    signed_in = login(b, "customer")
    preflight = b.client.options(PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "PATCH",
        "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token"})
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == ORIGIN
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert b.client.patch(PATH, headers=csrf_headers(signed_in), content="not-json").status_code == 415
    assert b.client.patch(PATH, headers=csrf_headers(signed_in), json={"name": "x" * 20_000}).status_code == 413
    assert b.client.get(PATH).json()["name"] == "Persona HTTP sintética customer"


def test_audit_failure_rolls_back_profile_before_http_success_and_allows_retry(backend, caplog):
    b = backend
    signed_in = login(b, "customer")
    before = b.client.get(PATH).json()
    def fail(mapper, connection, target):
        if target.action == "auth.update_profile":
            raise OperationalError("private-sql", {}, Exception("private-connection"))
    event.listen(AuditEvent, "before_insert", fail)
    try:
        response = b.client.patch(PATH, headers=csrf_headers(signed_in), json={"name": "Cambio transaccional"})
    finally:
        event.remove(AuditEvent, "before_insert", fail)
    assert response.status_code == 503 and "private-" not in response.text + caplog.text
    assert b.client.get(PATH).json() == before
    with b.sessions() as db:
        assert db.get(Person, b.ids["customer"][1]).updated_at is None
    assert b.client.patch(PATH, headers=csrf_headers(signed_in), json={"name": "Reintento"}).status_code == 200

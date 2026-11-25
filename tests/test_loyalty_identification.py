"""HU-042: QR por sesión, revisión del personal y consumo interno transaccional."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event

from alembic import command
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.core.auth_http import cookie_name
from app.core.loyalty_identification import generate_identification, hash_identification
from app.models import AccountSession, CustomerCoupon, Employee, LoyaltyIdentification, PersonalAccount
from app.schemas.loyalty_identification import LoyaltyIdentificationInput
from app.services import auth_service, loyalty_identification_service as service
from test_auth_api import csrf_headers, login, ORIGIN
from test_auth_persistence import config_for
from test_loyalty_identification_persistence import auth_backend, auth_database, backend, password_hash


ISSUE = "/api/auth/loyalty/identification"
VALIDATE = "/api/admin/loyalty/identifications/validate"


def issue(b, role="customer"):
    headers = csrf_headers(login(b, role))
    response = b.client.post(ISSUE, headers=headers, json={})
    assert response.status_code == 201, response.text
    return response.json(), (dict(b.client.cookies), headers)


def restore(b, credentials):
    b.client.cookies.clear()
    b.client.cookies.update(credentials[0])
    return credentials[1]


def validate(b, payload, role="staff"):
    b.client.cookies.clear()
    headers = csrf_headers(login(b, role))
    return b.client.post(VALIDATE, headers=headers, json={"qr_payload": payload})


@pytest.mark.parametrize("owner", ["customer", "admin", "staff"])
@pytest.mark.parametrize("validator", ["admin", "staff"])
def test_owner_issues_private_qr_and_personnel_can_review_it_without_consuming(owner, validator, backend):
    b = backend
    issued, _ = issue(b, owner)
    assert set(issued) == {"qr_payload", "issued_at", "expires_at", "identification_type", "purpose"}
    assert datetime.fromisoformat(issued["expires_at"]) - datetime.fromisoformat(issued["issued_at"]) == timedelta(minutes=5)
    assert issued["purpose"] == "visit_identification" and issued["identification_type"] == "qr"
    with b.sessions() as db:
        row = db.get(LoyaltyIdentification, b.ids[owner][0])
        assert row.token_hash == hash_identification(issued["qr_payload"])
        assert row.consumed_at is None
        before = db.execute(select(LoyaltyIdentification.__table__)).one()
    checked = validate(b, issued["qr_payload"], validator)
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert set(result) == {"valid", "account_id", "name", "expires_at"}
    assert result["valid"] is True and result["account_id"] == b.ids[owner][0]
    assert issued["qr_payload"] not in checked.text and b.emails[owner] not in checked.text
    assert checked.headers["cache-control"] == "no-store" and checked.headers["referrer-policy"] == "no-referrer"
    assert validate(b, issued["qr_payload"], validator).status_code == 200
    with b.sessions() as db:
        assert db.execute(select(LoyaltyIdentification.__table__)).one() == before
        assert db.scalar(select(func.count()).select_from(CustomerCoupon)) == 0


def test_renewal_replaces_the_code_and_resets_consumption_without_exposing_a_consume_endpoint(backend):
    b = backend
    first, credentials = issue(b)
    payload = LoyaltyIdentificationInput(qr_payload=first["qr_payload"])
    with b.sessions.begin() as db:
        service.consume_identification(db, payload, actor_account_id=b.ids["staff"][0], clock=lambda: b.now.value)
    assert validate(b, first["qr_payload"]).status_code == 409
    response = b.client.post(ISSUE, headers=restore(b, credentials), json={})
    assert response.status_code == 201, response.text
    second = response.json()
    assert second["qr_payload"] != first["qr_payload"]
    assert validate(b, first["qr_payload"]).status_code == 409
    assert validate(b, second["qr_payload"]).status_code == 200
    with b.sessions() as db:
        rows = db.scalars(select(LoyaltyIdentification)).all()
        assert len(rows) == 1 and rows[0].consumed_at is None and rows[0].consumed_by_account_id is None
    assert b.client.post("/api/admin/loyalty/identifications/consume", headers=csrf_headers(login(b, "staff")), json={"qr_payload": second["qr_payload"]}).status_code in {404, 405}


@pytest.mark.parametrize("limit", ["idle", "absolute"])
def test_qr_validity_never_outlives_its_emitting_session(backend, limit):
    b = backend
    headers = csrf_headers(login(b, "customer"))
    with b.sessions.begin() as db:
        session = db.scalar(select(AccountSession).where(AccountSession.account_id == b.ids["customer"][0]))
        until = b.now.value + timedelta(minutes=2)
        session.idle_expires_at = until
        if limit == "absolute":
            session.expires_at = until
    response = b.client.post(ISSUE, headers=headers, json={})
    assert response.status_code == 201, response.text
    assert datetime.fromisoformat(response.json()["expires_at"]) == until


def test_unknown_expired_and_consumed_credentials_use_the_same_generic_conflict(backend):
    b = backend
    issued, _ = issue(b)
    unknown = validate(b, generate_identification().qr_payload)
    assert unknown.status_code == 409
    with b.sessions.begin() as db:
        service.consume_identification(db, LoyaltyIdentificationInput(qr_payload=issued["qr_payload"]),
            actor_account_id=b.ids["staff"][0], clock=lambda: b.now.value)
    used = validate(b, issued["qr_payload"])
    assert used.status_code == 409 and used.json() == unknown.json()
    b.now.value += timedelta(minutes=5)
    expired = validate(b, issued["qr_payload"])
    assert expired.status_code == 409 and expired.json() == unknown.json()
    assert issued["qr_payload"] not in expired.text


def test_unused_qr_expires_at_its_exact_deadline(backend):
    b = backend
    issued, _ = issue(b)
    b.now.value += timedelta(minutes=5) - timedelta(seconds=1)
    assert validate(b, issued["qr_payload"]).status_code == 200
    b.now.value += timedelta(seconds=1)
    assert validate(b, issued["qr_payload"]).status_code == 409


@pytest.mark.parametrize("change", ["revoked", "auth_version", "inactive_account", "idle_expired", "session_binding", "future_activity", "future_issue", "inactive_employee"])
def test_validation_rechecks_account_session_and_credential_state(backend, change):
    b = backend
    owner = "admin" if change == "inactive_employee" else "customer"
    issued, _ = issue(b, owner)
    if change == "session_binding":
        login(b, "admin")
    with b.sessions.begin() as db:
        row = db.get(LoyaltyIdentification, b.ids[owner][0])
        session = db.get(AccountSession, row.session_id)
        if change == "revoked":
            session.revoked_at = b.now.value
        elif change == "auth_version":
            db.get(PersonalAccount, row.account_id).auth_version += 1
        elif change == "inactive_account":
            db.get(PersonalAccount, row.account_id).is_active = False
        elif change == "idle_expired":
            session.created_at = b.now.value - timedelta(hours=2)
            session.last_activity_at = b.now.value - timedelta(hours=1)
            session.idle_expires_at = b.now.value - timedelta(seconds=1)
        elif change == "session_binding":
            row.session_id = db.scalar(select(AccountSession.session_id).where(AccountSession.account_id == b.ids["admin"][0]))
        elif change == "future_activity":
            session.last_activity_at = b.now.value + timedelta(seconds=1)
        elif change == "future_issue":
            row.created_at = b.now.value + timedelta(seconds=1)
        else:
            db.get(Employee, b.ids[owner][1]).is_active = False
    response = validate(b, issued["qr_payload"])
    assert response.status_code == 409, response.text


@pytest.mark.parametrize("all_sessions", [False, True])
def test_logout_invalidates_the_qr_from_that_session(backend, all_sessions):
    b = backend
    issued, credentials = issue(b)
    headers = restore(b, credentials)
    route = "/api/auth/logout-all" if all_sessions else "/api/auth/logout"
    assert b.client.post(route, headers=headers, json={}).status_code == 204
    assert validate(b, issued["qr_payload"]).status_code == 409


def test_login_rotation_in_the_same_browser_invalidates_the_previous_qr(backend):
    b = backend
    issued, _ = issue(b)
    assert login(b, "customer").status_code == 200
    assert validate(b, issued["qr_payload"]).status_code == 409


@pytest.mark.parametrize("body", [{"account_id": 1}, {"expires_at": "2099-01-01T00:00:00Z"},
                                 {"auth_version": 0}, {"qr_payload": "secret"}, {"session_id": "other"}])
def test_issuer_cannot_choose_identity_expiration_or_token(backend, body):
    b = backend
    headers = csrf_headers(login(b, "customer"))
    response = b.client.post(ISSUE, headers=headers, json=body)
    assert response.status_code == 422
    with b.sessions() as db:
        assert db.scalar(select(LoyaltyIdentification)) is None


def test_only_authenticated_owner_and_authorized_personnel_can_use_the_routes(backend, monkeypatch):
    b = backend
    assert b.client.post(ISSUE, headers={"Origin": ORIGIN}, json={}).status_code == 401
    issued, credentials = issue(b)
    assert b.client.post(ISSUE, headers={"Origin": ORIGIN}, json={}).status_code == 403
    assert b.client.post(ISSUE, headers=credentials[1] | {"Origin": "https://evil.invalid"}, json={}).status_code == 403
    assert b.client.post(VALIDATE, headers=credentials[1], json={"qr_payload": issued["qr_payload"]}).status_code == 403
    b.client.cookies.clear()
    assert b.client.post(VALIDATE, headers={"Origin": ORIGIN}, json={"qr_payload": issued["qr_payload"]}).status_code == 401
    headers = csrf_headers(login(b, "staff"))
    assert b.client.post(VALIDATE, headers={"Origin": ORIGIN}, json={"qr_payload": issued["qr_payload"]}).status_code == 403
    assert b.client.post(VALIDATE, headers=headers | {"Origin": "https://evil.invalid"}, json={"qr_payload": issued["qr_payload"]}).status_code == 403
    assert b.client.get("/api/admin/loyalty/goals").status_code == 403
    monkeypatch.setattr(auth_service, "permissions_for_roles", lambda *args, **kwargs: frozenset({"catalog:read"}))
    headers = csrf_headers(login(b, "staff"))
    assert b.client.post(VALIDATE, headers=headers, json={"qr_payload": issued["qr_payload"]}).status_code == 403


@pytest.mark.parametrize("body", [{}, {"qr_payload": None}, {"qr_payload": "123456"}, {"qr_payload": "https://example.test/menu"}, {"qr_payload": True}])
def test_invalid_scan_input_is_generic_and_never_echoes_the_payload(backend, body, caplog):
    b = backend
    headers = csrf_headers(login(b, "staff"))
    response = b.client.post(VALIDATE, headers=headers, json=body)
    assert response.status_code == 422 and response.json() == {"detail": "Identificación de fidelización inválida."}
    assert response.headers["cache-control"] == "no-store"
    assert "https://example.test/menu" not in response.text + caplog.text


def test_valid_qr_with_injected_fields_is_not_echoed_and_is_not_a_login_cookie(backend, caplog):
    b = backend
    issued, _ = issue(b)
    headers = csrf_headers(login(b, "staff"))
    response = b.client.post(VALIDATE, headers=headers, json={"qr_payload": issued["qr_payload"], "account_id": 1})
    assert response.status_code == 422 and issued["qr_payload"] not in response.text + caplog.text
    b.client.cookies.clear()
    b.client.cookies.set(cookie_name(b.config), issued["qr_payload"])
    assert b.client.get("/api/auth/me").status_code == 401


def test_internal_consumption_rolls_back_with_its_callers_transaction(backend):
    b = backend
    issued, _ = issue(b)
    payload = LoyaltyIdentificationInput(qr_payload=issued["qr_payload"])
    with pytest.raises(RuntimeError), b.sessions.begin() as db:
        service.consume_identification(db, payload, actor_account_id=b.ids["staff"][0], clock=lambda: b.now.value)
        raise RuntimeError("Simulación de visita que no se pudo guardar")
    assert validate(b, issued["qr_payload"]).status_code == 200
    with b.sessions.begin() as db:
        result = service.consume_identification(db, payload, actor_account_id=b.ids["staff"][0], clock=lambda: b.now.value)
        assert result.account_id == b.ids["customer"][0]
    assert validate(b, issued["qr_payload"]).status_code == 409


def test_two_internal_consumers_cannot_use_the_same_qr(backend):
    b = backend
    issued, _ = issue(b)
    payload = LoyaltyIdentificationInput(qr_payload=issued["qr_payload"])
    barrier = Barrier(2)
    def consume(role):
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                service.consume_identification(db, payload, actor_account_id=b.ids[role][0], clock=lambda: b.now.value)
            return "consumed"
        except AuthError as error:
            assert error.status_code == 409
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume, role) for role in ("admin", "staff")]
        assert sorted(future.result(timeout=15) for future in futures) == ["conflict", "consumed"]


def test_consumption_checks_the_clock_after_waiting_for_the_credential_lock(backend):
    b = backend
    issued, _ = issue(b)
    payload = LoyaltyIdentificationInput(qr_payload=issued["qr_payload"])
    waiting = Event()
    engine = b.sessions.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, many):
        if 'FROM "LoyaltyIdentifications"' in statement and "FOR UPDATE" in statement:
            waiting.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with b.sessions.begin() as db:
            db.scalar(select(LoyaltyIdentification).where(LoyaltyIdentification.account_id == b.ids["customer"][0]).with_for_update())
            event.listen(engine, "before_cursor_execute", capture)
            future = pool.submit(_consume, b, payload)
            assert waiting.wait(10)
            b.now.value += timedelta(minutes=5)
        response = future.result(timeout=15)
    event.remove(engine, "before_cursor_execute", capture)
    assert response == 409
    with b.sessions() as db:
        assert db.get(LoyaltyIdentification, b.ids["customer"][0]).consumed_at is None


def _consume(b, payload):
    try:
        with b.sessions.begin() as db:
            service.consume_identification(db, payload, actor_account_id=b.ids["staff"][0], clock=lambda: b.now.value)
        return 200
    except AuthError as error:
        return error.status_code


@pytest.mark.parametrize("fault", ["insert", "commit"])
def test_failed_rotation_keeps_the_previous_qr_and_does_not_leak_the_new_one(backend, caplog, monkeypatch, fault):
    b = backend
    first, credentials = issue(b)
    token = generate_identification()
    monkeypatch.setattr(service, "generate_identification", lambda: token)
    engine, wrote = b.service.sessions.kw["bind"], []
    def before(conn, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO "LoyaltyIdentifications"') and fault == "insert":
            raise OperationalError("private-qr-error", {}, Exception("private-qr-error"))
    def after(conn, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO "LoyaltyIdentifications"'):
            wrote.append(True)
    def commit(conn):
        if fault == "commit" and wrote:
            raise OperationalError("private-qr-error", {}, Exception("private-qr-error"))
    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "commit", commit)
    try:
        failed = b.client.post(ISSUE, headers=restore(b, credentials), json={})
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
        event.remove(engine, "commit", commit)
    assert failed.status_code == 503, failed.text
    assert token.qr_payload not in failed.text + caplog.text and "private-qr-error" not in failed.text + caplog.text
    assert validate(b, first["qr_payload"]).status_code == 200
    retry = b.client.post(ISSUE, headers=restore(b, credentials), json={})
    assert retry.status_code == 201 and retry.json()["qr_payload"] == token.qr_payload


def test_missing_migration_returns_503_for_issuance_and_validation(backend):
    b = backend
    issued, credentials = issue(b)
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261124_0016")
    try:
        assert b.client.post(ISSUE, headers=restore(b, credentials), json={}).status_code == 503
        assert validate(b, issued["qr_payload"]).status_code == 503
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261124_0017")


def test_private_cors_and_no_store_for_issuance(backend):
    b = backend
    issued, credentials = issue(b)
    response = b.client.post(ISSUE, headers=restore(b, credentials), json={})
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    for route in (ISSUE, VALIDATE):
        preflight = b.client.options(route, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token"})
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"
        assert b.client.options(route, headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"}).status_code == 400


def test_issuance_rechecks_session_expiration_after_waiting_for_the_slot_lock(backend):
    b = backend
    issued, credentials = issue(b)
    headers = restore(b, credentials)
    engine, waiting = b.service.sessions.kw["bind"], Event()
    def capture(conn, cursor, statement, parameters, context, many):
        if 'FROM "LoyaltyIdentifications"' in statement and "FOR UPDATE" in statement:
            waiting.set()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with b.sessions.begin() as db:
                db.scalar(select(LoyaltyIdentification).where(LoyaltyIdentification.account_id == b.ids["customer"][0]).with_for_update())
                future = pool.submit(b.client.post, ISSUE, headers=headers, json={})
                assert waiting.wait(10)
                b.now.value += timedelta(minutes=31)
            response = future.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 401, response.text
    with b.sessions() as db:
        assert db.get(LoyaltyIdentification, b.ids["customer"][0]).token_hash == hash_identification(issued["qr_payload"])

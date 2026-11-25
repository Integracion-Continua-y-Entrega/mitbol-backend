"""HU-043: confirmación manual y QR en una transacción, sin duplicados ni recompensas automáticas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from unittest.mock import Mock
from uuid import uuid4

from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.core.auth_http import cookie_name
from app.core.config import Settings
from app.core.loyalty_identification import generate_identification, hash_identification
from app.main import create_app
from app.models import CustomerCoupon, Employee, LoyaltyIdentification, LoyaltyVisit
from app.schemas.loyalty_identification import LoyaltyIdentificationInput
from app.services import auth_service, loyalty_identification_service, loyalty_visit_service as service
from test_auth_api import csrf_headers, login, ORIGIN
from test_auth_persistence import config_for
from test_loyalty_identification import issue, restore, validate
from test_loyalty_visit_persistence import auth_backend, auth_database, backend, password_hash


PATH = "/api/admin/loyalty/visits"


def register(b, issued, role="staff"):
    b.client.cookies.clear()
    headers = csrf_headers(login(b, role))
    return b.client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]})


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_authorized_registration_records_presence_and_consumes_only_that_qr(backend, role):
    b = backend
    issued, _ = issue(b)
    response = register(b, issued, role)
    assert response.status_code == 201, response.text
    result = response.json()
    assert set(result) == {"visit_id", "account_id", "visited_on", "registered_at", "restaurant_timezone", "registered_by_account_id"}
    assert result["account_id"] == b.ids["customer"][0] and result["registered_by_account_id"] == b.ids[role][0]
    assert result["restaurant_timezone"] == "America/Mexico_City"
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    assert issued["qr_payload"] not in response.text and "identification_hash" not in response.text
    with b.sessions() as db:
        credential = db.get(LoyaltyIdentification, b.ids["customer"][0])
        visit = db.scalar(select(LoyaltyVisit))
        assert credential.consumed_at == visit.registered_at == b.now.value
        assert visit.identification_hash == hash_identification(issued["qr_payload"])
        assert credential.consumed_by_account_id == visit.registered_by_account_id
        assert db.scalars(select(CustomerCoupon)).all() == []
    assert validate(b, issued["qr_payload"]).status_code == 409
    assert register(b, issued, role).status_code == 409


def test_new_qr_does_not_allow_a_second_visit_on_the_same_day_and_remains_unused(backend):
    b = backend
    first, credentials = issue(b)
    assert register(b, first).status_code == 201
    fresh = b.client.post("/api/auth/loyalty/identification", headers=restore(b, credentials), json={})
    assert fresh.status_code == 201
    second = fresh.json()
    rejected = register(b, second)
    assert rejected.status_code == 409 and rejected.json()["detail"] == "La cuenta ya tiene una visita registrada para este día."
    assert validate(b, second["qr_payload"]).status_code == 200
    with b.sessions() as db:
        assert len(db.scalars(select(LoyaltyVisit)).all()) == 1
        assert db.get(LoyaltyIdentification, b.ids["customer"][0]).consumed_at is None


def test_next_local_day_can_be_seconds_later_and_does_not_require_an_order(backend):
    b = backend
    b.now.value = datetime(2026, 11, 25, 5, 59, 59, tzinfo=timezone.utc)
    first, credentials = issue(b)
    before = register(b, first)
    assert before.status_code == 201 and before.json()["visited_on"] == "2026-11-24"
    b.now.value += timedelta(seconds=1)
    renewed = b.client.post("/api/auth/loyalty/identification", headers=restore(b, credentials), json={})
    assert renewed.status_code == 201
    after = register(b, renewed.json())
    assert after.status_code == 201 and after.json()["visited_on"] == "2026-11-25"
    with b.sessions() as db:
        assert len(db.scalars(select(LoyaltyVisit)).all()) == 2


def test_timezone_comes_from_server_settings_and_uses_the_consumption_instant(backend):
    b = backend
    b.now.value = datetime(2026, 11, 25, 1, tzinfo=timezone.utc)
    b.app.state.settings = b.config.model_copy(update={"restaurant_timezone": "UTC"})
    issued, _ = issue(b)
    response = register(b, issued)
    assert response.status_code == 201
    assert response.json()["visited_on"] == "2026-11-25" and response.json()["restaurant_timezone"] == "UTC"


def test_invalid_server_timezone_rolls_back_consumption(backend):
    b = backend
    issued, _ = issue(b)
    b.app.state.settings = b.config.model_copy(update={"restaurant_timezone": "Invalid/Restaurant"})
    assert register(b, issued).status_code == 503
    assert validate(b, issued["qr_payload"]).status_code == 200
    b.app.state.settings = b.config
    assert register(b, issued).status_code == 201


@pytest.mark.parametrize("changes", [{"account_id": 1}, {"visited_on": "2026-01-01"},
    {"registered_by_account_id": 1}, {"registered_at": "2026-01-01T00:00:00Z"}, {"restaurant_timezone": "UTC"},
    {"identification_hash": "a" * 64}, {"force": True}, {"qr_payload": "123456"}, {"qr_payload": None}])
def test_input_does_not_allow_identity_date_timezone_or_overrides(backend, changes, caplog):
    b = backend
    issued, _ = issue(b)
    headers = csrf_headers(login(b, "staff"))
    response = b.client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]} | changes)
    assert response.status_code == 422, response.text
    assert issued["qr_payload"] not in response.text + caplog.text
    assert response.json() == {"detail": "Identificación de fidelización inválida."}
    assert validate(b, issued["qr_payload"]).status_code == 200


@pytest.mark.parametrize("role", [None, "customer"])
def test_guests_and_customers_cannot_register_presence(backend, role):
    b = backend
    issued, _ = issue(b)
    b.client.cookies.clear()
    headers = csrf_headers(login(b, role)) if role else {"Origin": ORIGIN}
    response = b.client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]})
    assert response.status_code == (403 if role else 401)
    assert validate(b, issued["qr_payload"]).status_code == 200


def test_permission_is_distinct_from_preview_and_requires_origin_csrf_and_active_employee(backend, monkeypatch):
    b = backend
    issued, _ = issue(b)
    headers = csrf_headers(login(b, "staff"))
    for invalid in ({"Origin": ORIGIN}, headers | {"Origin": "https://evil.invalid"}, headers | {"X-CSRF-Token": "0" * 64}):
        assert b.client.post(PATH, headers=invalid, json={"qr_payload": issued["qr_payload"]}).status_code == 403
    monkeypatch.setattr(auth_service, "permissions_for_roles", lambda *args, **kwargs: frozenset({"catalog:read", "visits:validate"}))
    headers = csrf_headers(login(b, "staff"))
    assert b.client.post("/api/admin/loyalty/identifications/validate", headers=headers, json={"qr_payload": issued["qr_payload"]}).status_code == 200
    assert b.client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]}).status_code == 403
    with b.sessions.begin() as db:
        db.get(Employee, b.ids["staff"][1]).is_active = False
    assert b.client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]}).status_code == 401


@pytest.mark.parametrize("change", ["unknown", "expired", "logged_out", "inactive", "version"])
def test_registration_revalidates_the_qr_and_account(backend, change):
    b = backend
    issued, credentials = issue(b)
    if change == "unknown":
        issued["qr_payload"] = generate_identification().qr_payload
    elif change == "expired":
        b.now.value += timedelta(minutes=5)
    elif change == "logged_out":
        assert b.client.post("/api/auth/logout", headers=restore(b, credentials), json={}).status_code == 204
    else:
        from app.models import PersonalAccount
        with b.sessions.begin() as db:
            account = db.get(PersonalAccount, b.ids["customer"][0])
            if change == "inactive":
                account.is_active = False
            else:
                account.auth_version += 1
    assert register(b, issued).status_code == 409
    with b.sessions() as db:
        assert db.scalars(select(LoyaltyVisit)).all() == []


def test_native_qr_uniqueness_conflict_rolls_back_consumption(backend):
    b = backend
    issued, _ = issue(b)
    digest = hash_identification(issued["qr_payload"])
    with b.sessions.begin() as db:
        from test_loyalty_visit_persistence import record
        db.add(record(b, identification_hash=digest, registered_at=datetime(2026, 1, 1, 18, tzinfo=timezone.utc), visited_on=datetime(2026, 1, 1).date()))
    response = register(b, issued)
    assert response.status_code == 409 and response.json()["detail"] == "La identificación ya se utilizó para una visita."
    assert validate(b, issued["qr_payload"]).status_code == 200


@pytest.mark.parametrize("fault", ["insert", "commit"])
def test_failure_after_consumption_restores_the_qr_and_does_not_leak_sql_or_credentials(backend, caplog, fault):
    b = backend
    issued, _ = issue(b)
    headers = csrf_headers(login(b, "staff"))
    engine, wrote = b.service.sessions.kw["bind"], []
    def before(conn, cursor, statement, parameters, context, many):
        if fault == "insert" and statement.startswith('INSERT INTO "LoyaltyVisits"'):
            raise OperationalError("private-visit-error", {}, Exception("private-visit-error"))
    def after(conn, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO "LoyaltyVisits"'):
            wrote.append(True)
    def commit(conn):
        if fault == "commit" and wrote:
            raise OperationalError("private-visit-error", {}, Exception("private-visit-error"))
    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "commit", commit)
    try:
        response = b.client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]})
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
        event.remove(engine, "commit", commit)
    assert response.status_code == 503, response.text
    assert issued["qr_payload"] not in response.text + caplog.text and "private-visit-error" not in response.text + caplog.text
    with b.sessions() as db:
        assert db.get(LoyaltyIdentification, b.ids["customer"][0]).consumed_at is None
        assert db.scalars(select(LoyaltyVisit)).all() == []
    assert register(b, issued).status_code == 201


def test_two_registrations_of_the_same_qr_have_only_one_winner(backend):
    b = backend
    issued, _ = issue(b)
    payload, barrier = LoyaltyIdentificationInput(qr_payload=issued["qr_payload"]), Barrier(2)
    def save(role):
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                service.register_visit(db, payload, actor_account_id=b.ids[role][0], clock=lambda: b.now.value)
            return "saved"
        except AuthError as error:
            assert error.status_code == 409
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, role) for role in ("admin", "staff")]
        assert sorted(f.result(timeout=20) for f in futures) == ["conflict", "saved"]
    with b.sessions() as db:
        assert len(db.scalars(select(LoyaltyVisit)).all()) == 1


def test_cross_registrations_between_authenticated_employees_do_not_deadlock(backend):
    b = backend
    b.client.cookies.clear()
    admin_qr, admin_credentials = issue(b, "admin")
    b.client.cookies.clear()
    staff_qr, staff_credentials = issue(b, "staff")
    barrier = Barrier(2)
    def save(credentials, target, sync):
        try:
            with b.service.authenticated(credentials[0][cookie_name(b.config)], request_id=uuid4(),
                    csrf_header=credentials[1]["X-CSRF-Token"], require_csrf=True) as ctx:
                if sync:
                    barrier.wait(timeout=5)
                service.register_visit(ctx.db, LoyaltyIdentificationInput(qr_payload=target["qr_payload"]),
                    actor_account_id=ctx.principal.account_id, clock=lambda: b.now.value)
            return 201
        except AuthError as error:
            assert error.status_code == 409
            return 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, admin_credentials, staff_qr, True), pool.submit(save, staff_credentials, admin_qr, True)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == [201, 409]
    loser = (admin_credentials, staff_qr) if results[0] == 409 else (staff_credentials, admin_qr)
    assert save(*loser, False) == 201
    with b.sessions() as db:
        assert len(db.scalars(select(LoyaltyVisit)).all()) == 2


def test_authenticated_renewal_and_registration_do_not_form_a_foreign_key_deadlock(backend):
    b = backend
    issued, owner_credentials = issue(b)
    b.client.cookies.clear()
    headers = csrf_headers(login(b, "staff"))
    staff_credentials = (dict(b.client.cookies), headers)
    barrier = Barrier(2)
    def act(credentials, renew):
        try:
            with b.service.authenticated(credentials[0][cookie_name(b.config)], request_id=uuid4(),
                    csrf_header=credentials[1]["X-CSRF-Token"], require_csrf=True) as ctx:
                barrier.wait(timeout=5)
                if renew:
                    loyalty_identification_service.issue_identification(ctx, clock=lambda: b.now.value)
                else:
                    service.register_visit(ctx.db, LoyaltyIdentificationInput(qr_payload=issued["qr_payload"]),
                        actor_account_id=ctx.principal.account_id, clock=lambda: b.now.value)
            return 201
        except AuthError as error:
            assert error.status_code == 409
            return 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(act, owner_credentials, True), pool.submit(act, staff_credentials, False)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == [201, 409]
    with b.sessions() as db:
        assert len(db.scalars(select(LoyaltyVisit)).all()) == (1 if results[1] == 201 else 0)


def test_registration_uses_the_day_after_waiting_for_the_qr_lock(backend):
    b = backend
    b.now.value = datetime(2026, 11, 25, 5, 59, 59, tzinfo=timezone.utc)
    issued, _ = issue(b)
    headers = csrf_headers(login(b, "staff"))
    engine, waiting = b.service.sessions.kw["bind"], Event()
    def capture(conn, cursor, statement, parameters, context, many):
        if 'FROM "LoyaltyIdentifications"' in statement and "FOR UPDATE" in statement:
            waiting.set()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with b.sessions.begin() as db:
                db.scalar(select(LoyaltyIdentification).where(LoyaltyIdentification.account_id == b.ids["customer"][0]).with_for_update())
                future = pool.submit(b.client.post, PATH, headers=headers, json={"qr_payload": issued["qr_payload"]})
                assert waiting.wait(10)
                b.now.value += timedelta(seconds=1)
            response = future.result(timeout=20)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 201 and response.json()["visited_on"] == "2026-11-25", response.text


def test_missing_migration_does_not_consume_the_qr(backend):
    b = backend
    issued, _ = issue(b)
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261124_0017")
    try:
        assert register(b, issued).status_code == 503
        assert validate(b, issued["qr_payload"]).status_code == 200
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261125_0018")


def test_visits_work_without_catalog_administration_and_keep_private_cors(backend):
    b = backend
    issued, _ = issue(b)
    config = b.config.model_copy(update={"auth_catalog_enabled": False, "auth_images_enabled": False})
    app = create_app(config)
    app.state.auth_service.clock = lambda: b.now.value
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        headers = csrf_headers(login(b, "staff", client=client))
        response = client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]})
        assert response.status_code == 201, response.text
        assert client.get("/api/admin/loyalty/goals").status_code == 403
        preflight = client.options(PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token"})
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"
        assert client.options(PATH, headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"}).status_code == 400


@pytest.mark.parametrize("zone", ["", " UTC ", "+06:00", "../timezone", "x" * 65])
def test_settings_reject_malformed_timezone_names(zone):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused", restaurant_timezone=zone)


def test_invalid_service_input_is_rejected_before_database_use():
    db = Mock()
    with pytest.raises(AuthError) as error:
        service.register_visit(db, {"qr_payload": "123456"}, actor_account_id=1, clock=lambda: None)
    assert error.value.status_code == 422 and db.mock_calls == []

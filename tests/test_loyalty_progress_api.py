"""HU-047: consulta propia, sesión vigente y confirmación antes de responder."""

from datetime import timedelta

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.main import create_app
from app.models import AccountSession, CustomerCoupon, Employee, LoyaltyGoal, LoyaltyReward, LoyaltyVisit, PersonalAccount
from test_auth_api import login, ORIGIN
from test_auth_persistence import config_for
from test_loyalty_progress import (
    auth_backend, auth_database, backend, configuration_backend, coupon_backend, goals,
    order_backend, password_hash, payment_backend, visits,
)
from test_loyalty_identification import issue
from test_loyalty_visits import register


PATH = "/api/auth/loyalty/progress"


@pytest.mark.parametrize("role", ["customer", "staff", "admin"])
def test_all_roles_consult_only_their_own_progress_without_csrf(backend, role):
    b = backend
    identifiers = goals(b)
    visits(b, 4, role=role)
    other = "admin" if role != "admin" else "customer"
    visits(b, 8, role=other)
    login(b, role)
    response = b.client.get(PATH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"total_visits", "current_level", "next_goal", "pending_goals"}
    assert result["total_visits"] == 4
    assert result["current_level"] == {"goal_id": str(identifiers[0]), "level_name": "Nivel 3", "required_visits": 3}
    assert result["next_goal"]["goal_id"] == str(identifiers[1]) and result["next_goal"]["remaining_visits"] == 1
    assert [row["goal_id"] for row in result["pending_goals"]] == [str(identifiers[0])]
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    assert all(value not in response.text for value in (b.emails[role], '"account_id":', '"updated_by_account_id":', '"identification_hash":'))


def test_guest_cannot_read_progress(backend):
    response = backend.client.get(PATH)
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
    assert "total_visits" not in response.text


@pytest.mark.parametrize("query", ["account_id", "person_id", "customer_id", "total_visits"])
def test_identity_or_progress_query_parameters_are_rejected(backend, query):
    b = backend
    login(b, "customer")
    response = b.client.get(PATH, params={query: b.ids["admin"][0]})
    assert response.status_code == 422 and "total_visits" not in response.json()
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("state", ["inactive", "revoked", "expired", "version", "employee"])
def test_invalid_sessions_do_not_reveal_progress(backend, state):
    b = backend
    role = "staff" if state == "employee" else "customer"
    login(b, role)
    with b.sessions.begin() as db:
        account = db.get(PersonalAccount, b.ids[role][0])
        session = db.scalar(select(AccountSession).where(AccountSession.account_id == account.account_id))
        if state == "inactive":
            account.is_active = False
        elif state == "revoked":
            session.revoked_at = b.now.value
        elif state == "expired":
            b.now.value += timedelta(minutes=31)
        elif state == "version":
            account.auth_version += 1
        else:
            db.get(Employee, b.ids[role][1]).is_active = False
    response = b.client.get(PATH)
    assert response.status_code == 401 and "total_visits" not in response.text


def test_registration_immediately_updates_progress_without_reissuing_a_coupon(backend):
    b = backend
    identifiers = goals(b, (1, 3))
    issued, _ = issue(b)
    receipt = register(b, issued)
    assert receipt.status_code == 201 and receipt.json()["total_visits"] == 1, receipt.text
    login(b, "customer")
    with b.sessions() as db:
        before = {model.__tablename__: db.execute(select(model.__table__)).all()
            for model in (LoyaltyVisit, LoyaltyReward, CustomerCoupon, LoyaltyGoal)}
    for _ in range(2):
        response = b.client.get(PATH)
        assert response.status_code == 200
        result = response.json()
        assert result["total_visits"] == 1 and result["current_level"]["goal_id"] == str(identifiers[0])
        assert result["next_goal"]["remaining_visits"] == 2 and result["pending_goals"] == []
    with b.sessions() as db:
        for model in (LoyaltyVisit, LoyaltyReward, CustomerCoupon, LoyaltyGoal):
            assert db.execute(select(model.__table__)).all() == before[model.__tablename__]


def test_empty_program_is_a_valid_response_not_an_invented_level(backend):
    b = backend
    login(b, "customer")
    assert b.client.get(PATH).json() == {"total_visits": 0, "current_level": None, "next_goal": None, "pending_goals": []}


def test_private_cors_and_origin_checks_apply_without_catalog_administration(backend):
    b = backend
    config = b.config.model_copy(update={"auth_catalog_enabled": False, "auth_images_enabled": False})
    app = create_app(config)
    app.state.auth_service.clock = lambda: b.now.value
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        preflight = client.options(PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"
        assert client.options(PATH, headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "GET"}).status_code == 400
        login(b, "customer", client=client)
        response = client.get(PATH, headers={"Origin": ORIGIN})
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"
        assert client.get(PATH, headers={"Origin": "https://evil.invalid"}).status_code == 403


def test_missing_migration_returns_503_without_claiming_zero_visits(backend):
    b = backend
    login(b, "customer")
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261125_0018")
    try:
        response = b.client.get(PATH)
        assert response.status_code == 503 and "total_visits" not in response.text
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261125_0019")


@pytest.mark.parametrize("fault", ["read", "commit"])
def test_database_failure_is_sanitized_and_prevents_a_success_response(backend, fault):
    b = backend
    login(b, "customer")
    engine = b.service.sessions.kw["bind"]
    read = []
    def fail_query(conn, cursor, statement, parameters, context, executemany):
        if 'LEFT OUTER JOIN "LoyaltyGoals"' in statement:
            read.append(True)
            if fault == "read":
                raise OperationalError("private SQL", {}, Exception("private database"))
    def fail_commit(conn):
        if fault == "commit" and read:
            raise OperationalError("private commit", {}, Exception("private database"))
    event.listen(engine, "before_cursor_execute", fail_query)
    event.listen(engine, "commit", fail_commit)
    try:
        response = b.client.get(PATH)
    finally:
        event.remove(engine, "before_cursor_execute", fail_query)
        event.remove(engine, "commit", fail_commit)
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    assert "private" not in response.text and "total_visits" not in response.text
    assert b.client.get(PATH).status_code == 200


def test_progress_route_is_absent_when_authentication_is_disabled(backend):
    config = backend.config.model_copy(update={"auth_enabled": False, "auth_catalog_enabled": False})
    with TestClient(create_app(config), base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        assert client.get(PATH).status_code == 404

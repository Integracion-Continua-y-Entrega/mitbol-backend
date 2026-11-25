"""HU-049: billetera propia, estados temporales y consulta sin reserva ni canje."""

from datetime import timedelta

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.main import create_app
from app.models import AccountSession, CustomerCoupon, LoyaltyGoal, LoyaltyReward, LoyaltyVisit, PersonalAccount
from test_auth_api import login, ORIGIN
from test_auth_persistence import config_for
from test_loyalty_identification import issue
from test_loyalty_reward_emission import configure
from test_loyalty_visits import register
from test_wallet import (
    auth_backend, auth_database, backend, configuration_backend, coupon_backend,
    order_backend, password_hash, payment_backend,
)


PATH = "/api/auth/wallet"


@pytest.mark.parametrize("role", ["customer", "admin", "staff"])
def test_each_role_sees_only_its_own_wallet_without_csrf(backend, role):
    b = backend
    login(b, role)
    response = b.client.get(PATH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"coupons", "limit", "offset", "has_more", "can_redeem"}
    expected = [] if role == "staff" else [b.ids[role][0]]
    with b.sessions() as db:
        owned = db.scalars(select(CustomerCoupon.customer_coupon_id).where(CustomerCoupon.account_id.in_(expected))).all()
    assert [row["customer_coupon_id"] for row in result["coupons"]] == owned
    assert result["limit"] == 25 and result["offset"] == 0 and not result["has_more"] and not result["can_redeem"]
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    assert all(key not in response.text for key in ('"account_id":', '"session_id":', '"goal_snapshot":', '"identification_hash":'))


def test_guest_cannot_consult_wallet(backend):
    response = backend.client.get(PATH)
    assert response.status_code == 401 and "coupons" not in response.json()
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("params", [
    {"account_id": 1}, {"customer_id": 1}, {"person_id": 1}, {"status": "available"},
    {"limit": 0}, {"limit": 101}, {"limit": "true"}, {"offset": -1}, {"offset": "bad"},
])
def test_unknown_or_invalid_parameters_do_not_change_the_account(backend, params):
    b = backend
    login(b, "customer")
    response = b.client.get(PATH, params=params)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
    assert "coupons" not in response.json()


@pytest.mark.parametrize("state", ["inactive", "revoked", "expired", "version"])
def test_invalid_session_does_not_reveal_wallet(backend, state):
    b = backend
    login(b, "customer")
    with b.sessions.begin() as db:
        account = db.get(PersonalAccount, b.ids["customer"][0])
        session = db.scalar(select(AccountSession).where(AccountSession.account_id == account.account_id))
        if state == "inactive":
            account.is_active = False
        elif state == "revoked":
            session.revoked_at = b.now.value
        elif state == "expired":
            b.now.value += timedelta(minutes=31)
        else:
            account.auth_version += 1
    response = b.client.get(PATH)
    assert response.status_code == 401 and "coupons" not in response.json()


def test_reward_appears_after_visit_and_repeated_reads_do_not_change_business_records(backend):
    b = backend
    goal_id = configure(b)
    issued, _ = issue(b)
    receipt = register(b, issued)
    assert receipt.status_code == 201, receipt.text
    created = receipt.json()["rewards"][0]
    login(b, "customer")
    models = (CustomerCoupon, LoyaltyGoal, LoyaltyVisit, LoyaltyReward)
    with b.sessions() as db:
        before = {model.__tablename__: db.execute(select(model.__table__)).all() for model in models}
    first = b.client.get(PATH, params={"limit": 1})
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["has_more"] and len(page["coupons"]) == 1
    coupon = page["coupons"][0]
    assert coupon["customer_coupon_id"] == created["customer_coupon_id"]
    assert coupon["source"] == "loyalty_reward" and coupon["display_status"] == "available"
    assert coupon["loyalty_reward"]["reward_id"] == created["reward_id"]
    assert coupon["loyalty_reward"]["goal_id"] == str(goal_id) and coupon["loyalty_reward"]["goal_version"] == 1
    assert b.client.get(PATH, params={"limit": 1}).json() == page
    rest = b.client.get(PATH, params={"limit": 1, "offset": 1}).json()
    assert not rest["has_more"] and rest["coupons"][0]["source"] == "coupon"
    assert b.client.get(PATH, params={"offset": 999}).json()["coupons"] == []
    with b.sessions() as db:
        for model in models:
            assert db.execute(select(model.__table__)).all() == before[model.__tablename__]
    legacy = b.client.get("/api/auth/coupons").json()
    assert set(legacy) == {"coupons"} and len(legacy["coupons"]) == 2
    assert "display_status" not in legacy["coupons"][0]


@pytest.mark.parametrize("stored,window,expected", [
    ("available", "current", "available"), ("reserved", "current", "reserved"),
    ("redeemed", "expired", "redeemed"), ("available", "expired", "expired"),
    ("available", "future", "scheduled"),
])
def test_visible_lifecycle_does_not_claim_redemption_is_enabled(backend, stored, window, expected):
    b = backend
    with b.sessions.begin() as db:
        coupon = db.get(CustomerCoupon, b.coupon.customer_coupon_id)
        coupon.status = stored
        if window == "expired":
            coupon.expires_at = b.now.value
        elif window == "future":
            coupon.starts_at, coupon.expires_at = b.now.value + timedelta(days=1), b.now.value + timedelta(days=2)
    login(b, "customer")
    result = b.client.get(PATH).json()
    assert result["coupons"][0]["display_status"] == expected and not result["can_redeem"]
    assert result["coupons"][0]["status"] == stored


def test_private_origin_cors_and_catalog_flag_are_preserved(backend):
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
        assert client.get(PATH, headers={"Origin": "https://evil.invalid"}).status_code == 403


def test_missing_migration_returns_unavailable_instead_of_an_empty_wallet(backend):
    b = backend
    login(b, "customer")
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261125_0018")
    try:
        response = b.client.get(PATH)
        assert response.status_code == 503 and "coupons" not in response.json()
        assert b.client.get("/api/auth/coupons").status_code == 200
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261125_0019")


@pytest.mark.parametrize("fault", ["read", "commit"])
def test_read_and_commit_failures_prevent_a_successful_response(backend, fault):
    b = backend
    login(b, "customer")
    engine = b.service.sessions.kw["bind"]
    read = []
    def fail_query(conn, cursor, statement, parameters, context, executemany):
        if 'LEFT OUTER JOIN "LoyaltyRewards"' in statement and '"CustomerCoupons"' in statement:
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
    assert "private" not in response.text and "coupons" not in response.json()
    assert b.client.get(PATH).status_code == 200


def test_route_is_absent_when_authentication_is_disabled(backend):
    config = backend.config.model_copy(update={"auth_enabled": False, "auth_catalog_enabled": False})
    with TestClient(create_app(config), base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        assert client.get(PATH).status_code == 404

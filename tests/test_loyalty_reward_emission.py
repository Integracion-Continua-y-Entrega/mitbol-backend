"""HU-045: visitas, QR y recompensas se confirman juntos en PostgreSQL desechable."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

from alembic import command
import pytest
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import OperationalError

from app.models import CustomerCoupon, Discount, LoyaltyGoal, LoyaltyIdentification, LoyaltyReward, LoyaltyVisit
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY
from test_auth_persistence import config_for
from test_auth_api import csrf_headers, login
from test_loyalty_configuration_persistence import goal
from test_loyalty_reward_persistence import (
    auth_backend, auth_database, backend as reward_backend, configuration_backend, coupon_backend, order_backend,
    password_hash, payment_backend,
)
from test_loyalty_identification import issue, validate
from test_loyalty_visits import PATH, register


@pytest.fixture
def backend(reward_backend):
    """Este escenario empieza sin los cupones de selección sembrados por la fixture base."""
    b = reward_backend
    with b.sessions.begin() as db:
        db.execute(delete(CustomerCoupon))
    return b


def configure(b, **changes):
    with b.sessions.begin() as db:
        row = goal(b, **({"required_visits": 1, "is_active": True} | changes))
        db.add(row)
        db.flush()
        return row.goal_id


def next_visit(b):
    """La prueba avanza un día; emisión y fecha siguen pasando por el servidor."""
    b.now.value += timedelta(days=1)
    issued, _ = issue(b)
    return register(b, issued)


def test_threshold_emits_available_owned_coupon_and_preserves_total_and_snapshot(backend):
    b = backend
    identifier = configure(b)
    issued, _ = issue(b)
    response = register(b, issued)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["total_visits"] == 1 and len(result["rewards"]) == 1
    reward = result["rewards"][0]
    assert reward["goal_id"] == str(identifier)
    with b.sessions() as db:
        grant = db.get(LoyaltyReward, UUID(reward["reward_id"]))
        coupon = db.get(CustomerCoupon, reward["customer_coupon_id"])
        assert grant.visit_id == UUID(result["visit_id"]) and grant.total_visits == 1
        assert grant.goal_snapshot["goal_id"] == str(identifier) and grant.goal_version == 1
        assert coupon.account_id == grant.account_id == b.ids["customer"][0]
        assert coupon.status == "available" and coupon.conditions == {"scope": "order", "minimum_subtotal": None,
            "minimum_quantity": None, "modalities": None, "product_ids": None, "include_complements": None,
            "compatible_discount_ids": None}
        assert coupon.starts_at == grant.awarded_at == b.now.value
        assert coupon.expires_at == b.now.value + timedelta(days=30)
    assert register(b, issued).status_code == 409
    b.client.cookies.clear()
    login(b, "customer")
    wallet = b.client.get("/api/auth/coupons")
    assert wallet.status_code == 200
    assert [item["customer_coupon_id"] for item in wallet.json()["coupons"]] == [reward["customer_coupon_id"]]
    b.client.cookies.clear()
    login(b, "admin")
    assert b.client.get("/api/auth/coupons").json()["coupons"] == []
    second = next_visit(b)
    assert second.status_code == 201 and second.json()["total_visits"] == 2 and second.json()["rewards"] == []
    with b.sessions() as db:
        assert len(db.scalars(select(CustomerCoupon)).all()) == len(db.scalars(select(LoyaltyReward)).all()) == 1


def test_multiple_thresholds_and_late_activation_use_accumulated_visits(backend):
    b = backend
    identifiers = []
    with b.sessions.begin() as db:
        for threshold in (1, 2, 3):
            row = goal(b, required_visits=threshold, is_active=False)
            db.add(row)
            db.flush()
            identifiers.append(row.goal_id)
    issued, _ = issue(b)
    assert register(b, issued).json()["rewards"] == []
    assert next_visit(b).json()["total_visits"] == 2
    with b.sessions.begin() as db:
        for identifier in identifiers:
            db.get(LoyaltyGoal, identifier).is_active = True
    response = next_visit(b)
    assert response.status_code == 201, response.text
    assert response.json()["total_visits"] == 3
    assert [item["goal_id"] for item in response.json()["rewards"]] == [str(value) for value in identifiers]
    with b.sessions.begin() as db:
        row = db.get(LoyaltyGoal, identifiers[0])
        row.version += 1
        row.reward_name = "Regla editada"
    response = next_visit(b)
    assert response.json()["total_visits"] == 4 and response.json()["rewards"] == []
    with b.sessions() as db:
        assert all(row.name != "Regla editada" for row in db.scalars(select(CustomerCoupon)))


def test_unusable_achieved_reward_rolls_back_visit_and_qr_then_can_be_retried(backend):
    b = backend
    configure(b)
    with b.sessions.begin() as db:
        db.get(Discount, b.discount.discount_id).is_active = False
    issued, _ = issue(b)
    response = register(b, issued)
    assert response.status_code == 409, response.text
    with b.sessions() as db:
        assert db.scalar(select(LoyaltyVisit)) is None and db.scalar(select(LoyaltyReward)) is None
        assert db.scalar(select(CustomerCoupon)) is None
        assert db.get(LoyaltyIdentification, b.ids["customer"][0]).consumed_at is None
    assert validate(b, issued["qr_payload"]).status_code == 200
    with b.sessions.begin() as db:
        db.get(Discount, b.discount.discount_id).is_active = True
    assert register(b, issued).status_code == 201


@pytest.mark.parametrize("stage", ["coupon", "reward", "commit"])
def test_persistence_failures_leave_no_partial_visit_coupon_or_consumption(backend, stage):
    b = backend
    configure(b)
    issued, _ = issue(b)
    engine = b.service.sessions.kw["bind"]
    inserted = []
    def fail_sql(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO "CustomerCoupons"'):
            inserted.append(True)
        target = 'INSERT INTO "CustomerCoupons"' if stage == "coupon" else 'INSERT INTO "LoyaltyRewards"'
        if stage != "commit" and statement.startswith(target):
            raise OperationalError("private SQL", {}, Exception("synthetic"))
    def fail_commit(conn):
        if stage == "commit" and inserted:
            raise OperationalError("private commit", {}, Exception("synthetic"))
    event.listen(engine, "before_cursor_execute", fail_sql)
    event.listen(engine, "commit", fail_commit)
    try:
        response = register(b, issued)
    finally:
        event.remove(engine, "before_cursor_execute", fail_sql)
        event.remove(engine, "commit", fail_commit)
    assert response.status_code == 503, response.text
    assert issued["qr_payload"] not in response.text and "private" not in response.text
    with b.sessions() as db:
        for model in (LoyaltyVisit, LoyaltyReward, CustomerCoupon):
            assert db.scalar(select(model)) is None
        assert db.get(LoyaltyIdentification, b.ids["customer"][0]).consumed_at is None
    assert register(b, issued).status_code == 201


def test_missing_reward_schema_refuses_visit_without_consuming_identification(backend):
    b = backend
    issued, _ = issue(b)
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261125_0018")
    try:
        assert register(b, issued).status_code == 503
        assert validate(b, issued["qr_payload"]).status_code == 200
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261125_0019")


def test_catalog_mutation_conflict_rolls_back_and_can_be_retried(backend):
    b = backend
    configure(b)
    issued, _ = issue(b)
    with b.sessions.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CATALOG_SYNC_LOCK_KEY})
        assert register(b, issued).status_code == 409
    assert register(b, issued).status_code == 201


def test_two_requests_cannot_issue_two_rewards_for_one_visit(backend):
    b = backend
    configure(b)
    issued, _ = issue(b)
    b.client.cookies.clear()
    headers = csrf_headers(login(b, "staff"))
    barrier = Barrier(2)
    def send(_):
        barrier.wait(timeout=10)
        return b.client.post(PATH, headers=headers, json={"qr_payload": issued["qr_payload"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send, range(2))) == [201, 409]
    with b.sessions() as db:
        for model in (LoyaltyVisit, LoyaltyReward, CustomerCoupon):
            assert len(db.scalars(select(model)).all()) == 1

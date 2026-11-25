"""HU-045: concesiones sintéticas, integridad y migración sin emisión automática."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError

from app.db.schema_compatibility import validate_bridge_schema
from app.models import CustomerCoupon, LoyaltyGoal, LoyaltyIdentification, LoyaltyReward, LoyaltyVisit
from app.models import (AuthBase, Base, CommercialDiscountBase, CouponBase, LoyaltyBase,
    LoyaltyIdentificationBase, LoyaltyRewardBase, LoyaltyVisitBase, OrderBase, OrderPaymentBase,
    OrderStateBase, PasswordRecoveryBase)
from app.models.sync_run import SyncBase
from test_auth_persistence import config_for
from test_loyalty_configuration_persistence import (
    auth_backend, auth_database, backend as configuration_backend, coupon_backend,
    goal, order_backend, password_hash, payment_backend,
)
from test_loyalty_visit_persistence import POINT, record


REVISION = "20261125_0019"
PREVIOUS = "20261125_0018"


@pytest.fixture
def backend(configuration_backend):
    b = configuration_backend
    with b.sessions.kw["bind"].begin() as db:
        command.upgrade(config_for(db), REVISION)
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(LoyaltyReward))
            db.execute(delete(LoyaltyVisit))
            db.execute(delete(LoyaltyIdentification))


def evidence(b):
    """Solo prepara referencias sintéticas; no representa una concesión del servicio."""
    with b.sessions.begin() as db:
        rule, visit = goal(b, is_active=True), record(b)
        coupon = CustomerCoupon(account_id=b.ids["customer"][0], name="Beneficio sintético",
            discount_id=b.discount.discount_id, starts_at=POINT, expires_at=POINT + timedelta(days=30),
            conditions={"scope": "order"})
        db.add_all([rule, visit, coupon])
        db.flush()
        return dict(account_id=visit.account_id, goal_id=rule.goal_id, visit_id=visit.visit_id,
            customer_coupon_id=coupon.customer_coupon_id, goal_version=rule.version, required_visits=5,
            total_visits=5, awarded_at=POINT, goal_snapshot={"reward_name": rule.reward_name,
                "conditions": {"scope": "order"}})


def test_migration_matches_metadata_and_downgrade_preserves_preexisting_data(backend):
    b = backend
    data = evidence(b)
    with b.sessions.begin() as db:
        db.add(LoyaltyReward(**data))
    with b.sessions.kw["bind"].begin() as db:
        assert validate_bridge_schema(db) == REVISION
        metadata = [Base.metadata, AuthBase.metadata, SyncBase.metadata, OrderBase.metadata,
            PasswordRecoveryBase.metadata, CouponBase.metadata, CommercialDiscountBase.metadata,
            OrderStateBase.metadata, OrderPaymentBase.metadata, LoyaltyBase.metadata,
            LoyaltyIdentificationBase.metadata, LoyaltyVisitBase.metadata, LoyaltyRewardBase.metadata]
        assert not compare_metadata(MigrationContext.configure(db), metadata)
        command.downgrade(config_for(db), PREVIOUS)
        assert "LoyaltyRewards" not in inspect(db).get_table_names()
        assert validate_bridge_schema(db) == PREVIOUS
        assert db.execute(select(CustomerCoupon).where(
            CustomerCoupon.customer_coupon_id == data["customer_coupon_id"])).first() is not None
        assert db.execute(select(LoyaltyVisit)).first() is not None
        command.upgrade(config_for(db), REVISION)
        assert validate_bridge_schema(db) == REVISION
        assert db.execute(select(LoyaltyReward)).first() is None


def test_offline_migration_has_no_emission_backfill_or_grants():
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "LoyaltyRewards"' in sql
    assert "INSERT INTO" not in sql and "GRANT " not in sql


@pytest.mark.parametrize("changes", [
    {"goal_version": 0}, {"required_visits": 0}, {"total_visits": 4},
    {"total_visits": -1}, {"goal_snapshot": []}, {"goal_snapshot": None},
    {"account_id": 2147483647}, {"goal_id": uuid4()}, {"visit_id": uuid4()},
    {"customer_coupon_id": 9223372036854775807},
])
def test_invalid_evidence_is_rejected_by_postgresql(backend, changes):
    data = evidence(backend)
    with pytest.raises(IntegrityError), backend.sessions.begin() as db:
        db.add(LoyaltyReward(**(data | changes)))
        db.flush()


def test_goal_edits_do_not_rewrite_awarded_snapshot_or_allow_a_duplicate(backend):
    b = backend
    data = evidence(b)
    with b.sessions.begin() as db:
        db.add(LoyaltyReward(**data))
    with b.sessions.begin() as db:
        rule = db.get(LoyaltyGoal, data["goal_id"])
        rule.reward_name = "Meta editada"
        rule.required_visits = 9
        rule.version += 1
    with b.sessions() as db:
        award = db.scalar(select(LoyaltyReward))
        assert award.goal_snapshot == data["goal_snapshot"]
        assert award.goal_version == 1 and award.required_visits == 5 and award.total_visits == 5
    with pytest.raises(IntegrityError), b.sessions.begin() as db:
        db.add(LoyaltyReward(**(data | {"goal_version": 2})))
        db.flush()


def test_concurrent_grants_for_the_same_account_and_goal_have_one_winner(backend):
    b = backend
    data = evidence(b)
    barrier = Barrier(2)
    def insert(_):
        try:
            with b.sessions.begin() as db:
                coupon = CustomerCoupon(account_id=data["account_id"], name="Otro cupón sintético",
                    starts_at=POINT, expires_at=POINT + timedelta(days=30), conditions={"scope": "order"})
                db.add(coupon)
                db.flush()
                barrier.wait(timeout=10)
                db.add(LoyaltyReward(**(data | {"customer_coupon_id": coupon.customer_coupon_id})))
                db.flush()
            return "created"
        except IntegrityError as error:
            assert error.orig.diag.constraint_name == "uq_LoyaltyRewards_account_goal"
            return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insert, range(2))) == ["created", "duplicate"]
    with b.sessions() as db:
        assert len(db.scalars(select(LoyaltyReward)).all()) == 1


def test_a_coupon_cannot_back_two_distinct_goal_awards(backend):
    data = evidence(backend)
    with backend.sessions.begin() as db:
        another = goal(backend, required_visits=3)
        db.add(another)
        db.flush()
        another_id = another.goal_id
        db.add(LoyaltyReward(**data))
    with pytest.raises(IntegrityError) as error, backend.sessions.begin() as db:
        db.add(LoyaltyReward(**(data | {"goal_id": another_id, "required_visits": 3})))
        db.flush()
    assert error.value.orig.diag.constraint_name == "uq_LoyaltyRewards_coupon"


@pytest.mark.parametrize("model,key", [(LoyaltyGoal, "goal_id"), (LoyaltyVisit, "visit_id"),
    (CustomerCoupon, "customer_coupon_id")])
def test_evidence_prevents_deleting_its_references(backend, model, key):
    data = evidence(backend)
    with backend.sessions.begin() as db:
        db.add(LoyaltyReward(**data))
    with pytest.raises(IntegrityError), backend.sessions.begin() as db:
        db.execute(delete(model).where(getattr(model, key) == data[key]))


def test_rollback_does_not_leave_a_grant(backend):
    data = evidence(backend)
    with backend.sessions() as db:
        db.add(LoyaltyReward(**data))
        db.flush()
        db.rollback()
    with backend.sessions() as db:
        assert db.scalar(select(LoyaltyReward)) is None

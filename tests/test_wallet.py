"""HU-049: estados visibles, evidencia histórica y consulta sin efectos secundarios."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from alembic import command
import pytest
from sqlalchemy import select

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, LoyaltyGoal, LoyaltyReward, LoyaltyVisit
from app.services import coupon_service, wallet_service
from test_auth_persistence import config_for
from test_loyalty_identification import issue
from test_loyalty_reward_emission import configure
from test_loyalty_reward_persistence import (
    auth_backend, auth_database, backend, configuration_backend, coupon_backend, evidence,
    order_backend, password_hash, payment_backend,
)
from test_loyalty_visit_persistence import POINT
from test_loyalty_visits import register


def read(b, role="customer", **changes):
    with b.sessions() as db:
        return wallet_service.get_wallet(db, account_id=b.ids[role][0], now=b.now.value, **changes)


@pytest.mark.parametrize("stored,delta,expected", [
    ("available", -1, "scheduled"), ("available", 0, "available"),
    ("available", 59, "available"), ("available", 60, "expired"),
    ("reserved", 0, "reserved"), ("reserved", 59, "reserved"), ("reserved", 60, "expired"),
    ("redeemed", -1, "redeemed"), ("redeemed", 0, "redeemed"), ("redeemed", 60, "redeemed"),
    ("expired", -1, "expired"), ("expired", 0, "expired"), ("expired", 60, "expired"),
])
def test_display_status_respects_validity_boundaries_and_historical_redemption(stored, delta, expected):
    coupon = SimpleNamespace(status=stored, starts_at=POINT, expires_at=POINT + timedelta(seconds=60))
    assert wallet_service._display_status(coupon, POINT + timedelta(seconds=delta)) == expected


def test_ordinary_coupons_keep_conditions_amounts_and_have_no_loyalty_origin(backend):
    b = backend
    result = read(b)
    assert len(result.coupons) == 1 and not result.can_redeem
    coupon = result.coupons[0]
    assert coupon.customer_coupon_id == b.coupon.customer_coupon_id
    assert coupon.status == coupon.display_status == "available" and coupon.available
    assert coupon.source == "coupon" and coupon.loyalty_reward is None
    assert coupon.conditions.minimum_quantity == 5 and coupon.conditions.minimum_subtotal == 150
    assert coupon.discount_type == "FIXED_AMOUNT" and coupon.discount_value == 25
    assert read(b, "staff").coupons == []
    assert read(b, "admin").coupons[0].customer_coupon_id == b.foreign_coupon.customer_coupon_id


def test_generated_reward_keeps_its_origin_and_coupon_after_goal_edits(backend):
    b = backend
    goal_id = configure(b)
    issued, _ = issue(b)
    receipt = register(b, issued)
    assert receipt.status_code == 201, receipt.text
    reward_id = receipt.json()["rewards"][0]["reward_id"]
    first = read(b).coupons[0]
    assert first.source == "loyalty_reward" and str(first.loyalty_reward.reward_id) == reward_id
    assert first.loyalty_reward.goal_id == goal_id and first.loyalty_reward.goal_version == 1
    assert first.loyalty_reward.total_visits == first.loyalty_reward.required_visits == 1
    assert first.loyalty_reward.awarded_at == first.starts_at == b.now.value
    with b.sessions.begin() as db:
        rule = db.get(LoyaltyGoal, goal_id)
        rule.version += 1
        rule.reward_name, rule.valid_days, rule.is_active = "Configuración posterior", 1, False
        rule.conditions = {"scope": "order", "minimum_quantity": 100}
    assert read(b).coupons[0] == first
    public = first.model_dump_json()
    assert all(key not in public for key in ('"account_id":', '"visit_id":', '"identification_hash":', '"goal_snapshot":'))


@pytest.mark.parametrize("stored", ["available", "reserved", "redeemed", "expired"])
def test_expiration_is_read_only_and_preserves_the_stored_state(backend, stored):
    b = backend
    with b.sessions.begin() as db:
        coupon = db.get(CustomerCoupon, b.coupon.customer_coupon_id)
        coupon.status, coupon.expires_at = stored, b.now.value
    result = read(b).coupons[0]
    assert result.status == stored and not result.available
    assert result.display_status == ("redeemed" if stored == "redeemed" else "expired")
    with b.sessions() as db:
        assert db.get(CustomerCoupon, b.coupon.customer_coupon_id).status == stored


def test_future_coupon_is_scheduled_and_not_available(backend):
    b = backend
    with b.sessions.begin() as db:
        coupon = db.get(CustomerCoupon, b.coupon.customer_coupon_id)
        coupon.starts_at, coupon.expires_at = b.now.value + timedelta(days=1), b.now.value + timedelta(days=2)
    result = read(b).coupons[0]
    assert result.status == "available" and result.display_status == "scheduled" and not result.available


def test_pagination_is_owned_newest_first_and_reports_more_without_a_false_total(backend):
    b = backend
    with b.sessions.begin() as db:
        extra = [CustomerCoupon(account_id=b.ids["customer"][0], name=f"Cupón sintético {index}",
            starts_at=b.now.value, expires_at=b.now.value + timedelta(days=1), conditions={"scope": "order"})
            for index in range(3)]
        db.add_all(extra)
        db.flush()
        expected = sorted([b.coupon.customer_coupon_id, *(row.customer_coupon_id for row in extra)], reverse=True)
    first, last, empty = read(b, limit=2), read(b, limit=2, offset=2), read(b, offset=999)
    assert [row.customer_coupon_id for row in first.coupons + last.coupons] == expected
    assert first.has_more and not last.has_more and not empty.has_more and empty.coupons == []
    assert first.limit == last.limit == 2 and first.offset == 0 and last.offset == 2


def test_foreign_reward_link_does_not_expose_foreign_evidence_or_coupons(backend):
    b = backend
    data = evidence(b)
    with b.sessions.begin() as db:
        db.add(LoyaltyReward(**(data | {"account_id": b.ids["admin"][0]})))
    own = read(b)
    linked = next(row for row in own.coupons if row.customer_coupon_id == data["customer_coupon_id"])
    assert linked.source == "coupon" and linked.loyalty_reward is None
    assert all(row.customer_coupon_id != data["customer_coupon_id"] for row in read(b, "admin").coupons)


def test_undefined_conditions_stay_pending_without_becoming_unrestricted(backend):
    b = backend
    with b.sessions.begin() as db:
        db.get(CustomerCoupon, b.coupon.customer_coupon_id).conditions = None
    coupon = read(b).coupons[0]
    assert coupon.conditions is None and coupon.conditions_status == "pending"
    assert not read(b).can_redeem


def test_consultation_does_not_modify_coupons_visits_goals_or_concessions(backend):
    b = backend
    models = (CustomerCoupon, LoyaltyVisit, LoyaltyGoal, LoyaltyReward)
    def snapshot():
        with b.sessions() as db:
            return {model.__tablename__: db.execute(select(model.__table__)).all() for model in models}
    before = snapshot()
    assert read(b) == read(b)
    assert snapshot() == before


def test_missing_loyalty_schema_does_not_change_the_existing_coupon_contract(backend):
    b = backend
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261125_0018")
    try:
        with pytest.raises(AuthError) as error:
            read(b)
        assert error.value.status_code == 503
        with b.sessions() as db:
            legacy = coupon_service.list_coupons(db, b.ids["customer"][0], now=b.now.value)
        assert len(legacy) == 1 and legacy[0].customer_coupon_id == b.coupon.customer_coupon_id
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261125_0019")


@pytest.mark.parametrize("changes", [
    {"account_id": 0}, {"account_id": True}, {"limit": 0}, {"limit": 101},
    {"limit": True}, {"offset": -1}, {"offset": "1"}, {"now": None}, {"now": datetime(2026, 11, 25)},
])
def test_invalid_arguments_are_rejected_without_database_access(changes):
    db = Mock()
    with pytest.raises(AuthError):
        wallet_service.get_wallet(db, **({"account_id": 1, "now": POINT} | changes))
    assert db.mock_calls == []

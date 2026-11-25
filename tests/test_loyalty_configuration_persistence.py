"""HU-046: contrato y almacenamiento de reglas sintéticas, sin emitir beneficios."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db.schema_compatibility import validate_bridge_schema
from app.models import (
    AuthBase, Base, CommercialDiscountBase, CouponBase, CustomerCoupon, Discount, LoyaltyBase,
    LoyaltyGoal, Order, OrderBase, OrderPaymentBase, OrderStateBase, PasswordRecoveryBase,
)
from app.models.sync_run import SyncBase
from app.schemas.loyalty import LoyaltyGoalInput, LoyaltyGoalRead
from test_auth_persistence import config_for
from test_order_payment_persistence import (
    auth_backend, auth_database, backend as payment_backend, coupon_backend, order_backend, password_hash,
)
from test_staff_order_management import guest_order


REVISION = "20261124_0016"
PREVIOUS = "20261124_0015"


def payload(**changes):
    """Ejemplo exclusivo de pruebas; no configura el programa real del restaurante."""
    return {"required_visits": 5, "level_name": "Nivel sintético", "reward_name": "Recompensa sintética",
            "discount_id": 1, "valid_days": 30, "conditions": {"scope": "order"}} | changes


@pytest.fixture
def backend(payment_backend):
    b = payment_backend
    with b.sessions.kw["bind"].begin() as db:
        command.upgrade(config_for(db), REVISION)
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(LoyaltyGoal))


def goal(b, **changes):
    rule = LoyaltyGoalInput.model_validate(payload(discount_id=b.discount.discount_id))
    return LoyaltyGoal(**(rule.model_dump(mode="json") | {"updated_by_account_id": b.ids["admin"][0]} | changes))


def test_contract_normalizes_names_and_requires_explicit_coupon_conditions():
    rule = LoyaltyGoalInput.model_validate(payload(level_name="  Nivel 🍔  ", reward_name="  Beneficio  ", reward_description="  "))
    assert rule.level_name == "Nivel 🍔" and rule.reward_name == "Beneficio"
    assert rule.reward_description is None and rule.is_active is False
    assert rule.conditions.scope == "order"
    with pytest.raises(ValidationError):
        LoyaltyGoalInput.model_validate(payload(conditions={}))


@pytest.mark.parametrize("changes", [
    {"required_visits": 0}, {"required_visits": -1}, {"required_visits": True}, {"required_visits": "5"},
    {"required_visits": 2147483648}, {"valid_days": 0}, {"valid_days": 32768}, {"valid_days": False},
    {"level_name": " "}, {"level_name": "x" * 51}, {"level_name": "Nivel\nincorrecto"},
    {"reward_name": ""}, {"reward_name": "x" * 101}, {"reward_name": "nul\x00"},
    {"reward_description": "x" * 1001}, {"discount_id": 0}, {"discount_id": True}, {"is_active": "true"},
    {"conditions": None}, {"conditions": {"scope": "order", "unknown": True}},
    {"conditions": {"scope": "products"}}, {"conditions": {"scope": "order", "minimum_subtotal": "-1"}},
    {"conditions": {"scope": "order", "minimum_quantity": 0}},
    {"goal_id": str(uuid4())}, {"version": 2}, {"updated_by_account_id": 1},
])
def test_contract_rejects_incoherent_rules_and_server_controlled_fields(changes):
    with pytest.raises(ValidationError):
        LoyaltyGoalInput.model_validate(payload(**changes))


def test_product_conditions_preserve_precise_minimum_amount_and_explicit_scope():
    rule = LoyaltyGoalInput.model_validate(payload(conditions={"scope": "products", "product_ids": [1],
        "include_complements": False, "minimum_subtotal": "150.123456789012345678901", "modalities": ["pickup"]}))
    assert rule.conditions.minimum_subtotal == Decimal("150.123456789012345678901")
    assert rule.model_dump(mode="json")["conditions"]["minimum_subtotal"] == "150.123456789012345678901"


def test_incremental_sql_does_not_seed_rules_or_grant_access(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La migración offline intentó conectarse")
    monkeypatch.setattr(Engine, "connect", forbidden)
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "LoyaltyGoals"' in sql
    assert "INSERT INTO" not in sql and "GRANT " not in sql and "DROP TABLE" not in sql


def test_migration_matches_model_and_preserves_existing_orders_and_coupons(backend):
    b = backend
    receipt = guest_order(b)
    with b.sessions.kw["bind"].begin() as db:
        assert validate_bridge_schema(db) == REVISION
        metadata = [Base.metadata, AuthBase.metadata, SyncBase.metadata, OrderBase.metadata,
            PasswordRecoveryBase.metadata, CouponBase.metadata, CommercialDiscountBase.metadata,
            OrderStateBase.metadata, OrderPaymentBase.metadata, LoyaltyBase.metadata]
        assert not compare_metadata(MigrationContext.configure(db, opts={"compare_server_default": True}), metadata)
        assert db.execute(select(LoyaltyGoal)).first() is None
        coupons = db.execute(select(CustomerCoupon.__table__)).all()
        orders = db.execute(select(Order.__table__)).all()
        assert str(orders[0].order_id) == receipt["order_id"]
        command.downgrade(config_for(db), PREVIOUS)
        assert "LoyaltyGoals" not in inspect(db).get_table_names()
        assert validate_bridge_schema(db) == PREVIOUS
        command.upgrade(config_for(db), REVISION)
        assert validate_bridge_schema(db) == REVISION
        assert db.execute(select(CustomerCoupon.__table__)).all() == coupons
        assert db.execute(select(Order.__table__)).all() == orders


def test_rule_defaults_and_read_contract_do_not_generate_coupons(backend):
    b = backend
    with b.sessions.begin() as db:
        before = db.execute(select(CustomerCoupon.__table__)).all()
        rule = goal(b)
        db.add(rule)
        db.flush()
        read = LoyaltyGoalRead.model_validate(rule)
        assert read.required_visits == 5 and read.version == 1 and read.is_active is False
        assert read.updated_by_account_id == b.ids["admin"][0]
        assert read.created_at.tzinfo is not None and read.updated_at.tzinfo is not None
        assert read.conditions.scope == "order" and read.discount_id == b.discount.discount_id
        assert db.execute(select(CustomerCoupon.__table__)).all() == before


@pytest.mark.parametrize("changes,constraint", [
    ({"required_visits": 0}, "ck_LoyaltyGoals_visits"),
    ({"required_visits": -1}, "ck_LoyaltyGoals_visits"),
    ({"valid_days": 0}, "ck_LoyaltyGoals_valid_days"),
    ({"version": 0}, "ck_LoyaltyGoals_version"),
    ({"level_name": " "}, "ck_LoyaltyGoals_level_name"),
    ({"reward_name": " "}, "ck_LoyaltyGoals_reward_name"),
    ({"conditions": []}, "ck_LoyaltyGoals_conditions"),
    ({"conditions": None}, "ck_LoyaltyGoals_conditions"),
    ({"discount_id": -1}, "fk_LoyaltyGoals_discount_id"),
    ({"updated_by_account_id": -1}, "fk_LoyaltyGoals_updated_by_account_id"),
])
def test_database_rejects_invalid_stored_rules(backend, changes, constraint):
    b = backend
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.add(goal(b, **changes))
        db.flush()
    assert error.value.orig.diag.constraint_name == constraint


def test_two_concurrent_rules_cannot_define_the_same_visit_threshold(backend):
    b = backend
    barrier = Barrier(2)
    def save():
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                db.add(goal(b))
                db.flush()
            return "saved"
        except IntegrityError as error:
            assert error.orig.diag.constraint_name == "uq_LoyaltyGoals_required_visits"
            return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(save) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in results) == ["duplicate", "saved"]


def test_referenced_discount_cannot_be_deleted(backend):
    b = backend
    with b.sessions.begin() as db:
        discount = Discount(loyverse_id=uuid4().hex, name="Beneficio solo para prueba de FK",
                            discount_type="FIXED_AMOUNT", value=Decimal("10"), scope="unknown", is_active=True)
        db.add(discount)
        db.flush()
        identifier = discount.discount_id
        db.add(goal(b, discount_id=identifier))
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.execute(delete(Discount).where(Discount.discount_id == identifier))
    assert error.value.orig.diag.constraint_name == "fk_LoyaltyGoals_discount_id"

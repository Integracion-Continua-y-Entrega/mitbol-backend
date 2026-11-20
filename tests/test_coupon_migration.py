"""Una tabla, sin emisiones ni cambios externos; roundtrip en PG desechable."""

from datetime import datetime, timedelta, timezone
from io import StringIO

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.models import AuthBase, Base, CouponBase, CustomerCoupon, Discount, OrderBase, PasswordRecoveryBase, Person, PersonalAccount
from app.models.sync_run import SyncBase
from test_auth_persistence import config_for


PREVIOUS = "20261119_0011"
REVISION = "20261119_0012"


def test_delta_creates_only_personal_coupons_without_issuing_rewards_or_changing_permissions():
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "CustomerCoupons"' in sql
    assert not any(value in sql for value in ("CREATE ROLE", "GRANT ", "INSERT INTO", "ALTER TABLE", "DROP TABLE"))


def test_upgrade_enforces_ownership_relations_and_validity_and_roundtrips_without_changing_existing_data(empty_postgresql_connection):
    db = empty_postgresql_connection
    config = config_for(db)
    command.upgrade(config, PREVIOUS)
    before = set(inspect(db).get_table_names())
    person_id = db.scalar(Person.__table__.insert().values(name="Identidad sintética", phone="2281234567").returning(Person.person_id))
    account_id = db.scalar(PersonalAccount.__table__.insert().values(person_id=person_id, email="coupon@example.test",
        password_hash="$argon2id$synthetic", is_active=True).returning(PersonalAccount.account_id))
    discount_id = db.scalar(Discount.__table__.insert().values(loyverse_id="synthetic-coupon-discount", name="Beneficio sintético",
        discount_type="FIXED_AMOUNT", value=25, scope="unknown").returning(Discount.discount_id))
    command.upgrade(config, REVISION)
    assert set(inspect(db).get_table_names()) == before | {"CustomerCoupons"}
    assert compare_metadata(MigrationContext.configure(db), [Base.metadata, AuthBase.metadata, SyncBase.metadata,
                            OrderBase.metadata, PasswordRecoveryBase.metadata, CouponBase.metadata]) == []
    assert {fk["referred_table"] for fk in inspect(db).get_foreign_keys("CustomerCoupons")} == {"PersonalAccounts", "Discounts"}
    now = datetime.now(timezone.utc)
    values = dict(account_id=account_id, name="Cupón sintético", discount_id=discount_id,
                  starts_at=now, expires_at=now + timedelta(days=1), conditions={"scope": "order"})
    for changes in ({"account_id": account_id + 1000}, {"discount_id": discount_id + 1000}, {"status": "invalid"},
                    {"name": " "}, {"expires_at": now}, {"conditions": []}):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(CustomerCoupon.__table__.insert().values(values | changes))
    identifier = db.scalar(CustomerCoupon.__table__.insert().values(values).returning(CustomerCoupon.customer_coupon_id))
    assert db.scalar(select(CustomerCoupon.status).where(CustomerCoupon.customer_coupon_id == identifier)) == "available"
    command.downgrade(config, PREVIOUS)
    assert set(inspect(db).get_table_names()) == before
    assert db.scalar(select(PersonalAccount.email).where(PersonalAccount.account_id == account_id)) == "coupon@example.test"
    assert db.scalar(select(Discount.value).where(Discount.discount_id == discount_id)) == 25
    command.upgrade(config, REVISION)
    assert db.scalar(select(CustomerCoupon.customer_coupon_id)) is None

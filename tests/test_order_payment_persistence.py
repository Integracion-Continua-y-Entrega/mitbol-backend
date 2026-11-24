"""HU-031, bloque de persistencia: restricciones reales y migración reversible."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
from threading import Barrier
from uuid import UUID, uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.db.schema_compatibility import validate_bridge_schema
from app.models import (
    AuthBase, Base, CommercialDiscountBase, CouponBase, Order, OrderBase,
    OrderDiscountSnapshot, OrderPayment, OrderPaymentBase, OrderStateBase,
    OrderStatusHistory, PasswordRecoveryBase,
)
from app.models.sync_run import SyncBase
from test_auth_persistence import config_for
from test_staff_order_management import (
    auth_backend, auth_database, backend as order_backend, coupon_backend,
    csrf_headers, decide, guest_order, login, password_hash,
)


REVISION = "20261124_0015"
PREVIOUS = "20261121_0014"


@pytest.fixture
def backend(order_backend):
    b = order_backend
    with b.sessions.kw["bind"].begin() as db:
        command.upgrade(config_for(db), REVISION)
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(OrderPayment))


def payment(b, receipt, **changes):
    """Construye evidencia sintética; el endpoint posterior determinará importe y autorización."""
    return OrderPayment(**({"order_id": UUID(receipt["order_id"]), "amount": Decimal(receipt["total"]),
        "method": "cash", "verified_by_account_id": b.ids["staff"][0]} | changes))


def test_incremental_migration_is_offline_and_does_not_create_payments_or_permissions(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La generación offline intentó conectarse a una base")
    monkeypatch.setattr(Engine, "connect", forbidden)
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "OrderPayments"' in sql
    assert "INSERT INTO" not in sql and "GRANT " not in sql
    assert 'UPDATE "Orders"' not in sql and "DROP TABLE" not in sql


def test_schema_matches_model_and_downgrade_preserves_orders_and_history(backend):
    b = backend
    receipt = guest_order(b)
    with b.sessions.kw["bind"].begin() as db:
        assert validate_bridge_schema(db) == REVISION
        metadata = [Base.metadata, AuthBase.metadata, SyncBase.metadata, OrderBase.metadata,
                    PasswordRecoveryBase.metadata, CouponBase.metadata, CommercialDiscountBase.metadata,
                    OrderStateBase.metadata, OrderPaymentBase.metadata]
        assert not compare_metadata(MigrationContext.configure(db), metadata)
        assert db.execute(select(OrderPayment)).first() is None
        command.downgrade(config_for(db), PREVIOUS)
        assert "OrderPayments" not in inspect(db).get_table_names()
        assert validate_bridge_schema(db) == PREVIOUS
        assert db.scalar(select(Order.total).where(Order.order_id == UUID(receipt["order_id"]))) == Decimal(receipt["total"])
        assert len(db.execute(select(OrderStatusHistory)).all()) == 1
        command.upgrade(config_for(db), REVISION)
        assert validate_bridge_schema(db) == REVISION


@pytest.mark.parametrize("method", ["cash", "card", "transfer", "other"])
def test_payment_preserves_amount_actor_time_and_operational_state(backend, method):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b, "staff"))
    assert decide(b, receipt, headers).status_code == 200
    with b.sessions.begin() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        snapshot = dict(db.get(OrderDiscountSnapshot, order.order_id).calculation)
        record = payment(b, receipt, method=method)
        db.add(record)
        db.flush()
        assert record.verified_at.tzinfo is not None
        saved = (record.payment_id, record.amount, record.method, record.verified_at, record.verified_by_account_id)
        assert order.status == "Confirmado" and order.total == record.amount
        assert len(db.scalars(select(OrderStatusHistory)).all()) == 2
        assert db.get(OrderDiscountSnapshot, order.order_id).calculation == snapshot
    prepared = b.client.post(f'/api/admin/orders/{receipt["order_id"]}/prepare', headers=headers, json={})
    assert prepared.status_code == 200
    with b.sessions() as db:
        record = db.scalar(select(OrderPayment))
        assert (record.payment_id, record.amount, record.method, record.verified_at, record.verified_by_account_id) == saved


@pytest.mark.parametrize("changes,constraint", [
    ({"amount": Decimal("0")}, "ck_OrderPayments_amount"),
    ({"amount": Decimal("-1")}, "ck_OrderPayments_amount"),
    ({"amount": Decimal("NaN")}, "ck_OrderPayments_amount"),
    ({"amount": Decimal("Infinity")}, "ck_OrderPayments_amount"),
    ({"amount": Decimal("-Infinity")}, "ck_OrderPayments_amount"),
    ({"method": "unknown"}, "ck_OrderPayments_method"),
    ({"order_id": UUID(int=0)}, "fk_OrderPayments_order_id"),
    ({"verified_by_account_id": -1}, "fk_OrderPayments_verified_by_account_id"),
])
def test_database_rejects_invalid_evidence(backend, changes, constraint):
    b = backend
    receipt = guest_order(b)
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.add(payment(b, receipt, **changes))
        db.flush()
    assert error.value.orig.diag.constraint_name == constraint
    with b.sessions() as db:
        assert db.scalar(select(OrderPayment)) is None
        assert db.get(Order, UUID(receipt["order_id"])).status == "Solicitud recibida"


def test_payment_prevents_deleting_its_order(backend):
    b = backend
    receipt = guest_order(b)
    with b.sessions.begin() as db:
        db.add(payment(b, receipt))
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.execute(delete(Order).where(Order.order_id == UUID(receipt["order_id"])))
    assert error.value.orig.diag.constraint_name == "fk_OrderPayments_order_id"


def test_two_concurrent_confirmations_cannot_duplicate_payment(backend):
    b = backend
    receipt = guest_order(b)
    barrier = Barrier(2)
    def save():
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                db.add(payment(b, receipt, payment_id=uuid4()))
                db.flush()
            return "saved"
        except IntegrityError as error:
            assert error.orig.diag.constraint_name == "uq_OrderPayments_order_id"
            return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(save) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in results) == ["duplicate", "saved"]
    with b.sessions() as db:
        assert len(db.scalars(select(OrderPayment)).all()) == 1

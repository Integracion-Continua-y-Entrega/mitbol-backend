"""HU-031: cobro manual protegido, sin inferirlo del avance operativo ni duplicarlo."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import Mock
from uuid import UUID, uuid4

from alembic import command
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, Employee, Order, OrderDiscountSnapshot, OrderPayment, OrderStatusHistory, ProductVariant
from app.schemas.order_payments import OrderPaymentInput
from app.services import auth_service, order_payment_service
from test_auth_api import csrf_headers, login, ORIGIN
from test_order_payment_persistence import (
    auth_backend, auth_database, backend, coupon_backend, order_backend, password_hash,
)
from test_order_preparation import accepted_order, advance
from test_staff_order_management import guest_order
from test_auth_persistence import config_for


def path(receipt):
    return f'/api/admin/orders/{receipt["order_id"]}/payment'


def confirm(b, receipt, headers, *, amount=None, method="cash"):
    return b.client.post(path(receipt), headers=headers,
                         json={"amount": receipt["total"] if amount is None else amount, "method": method})


@pytest.mark.parametrize("role", ["admin", "staff"])
@pytest.mark.parametrize("method", ["cash", "card", "transfer", "other"])
def test_authorized_confirmation_is_auditable_and_preserves_the_quote(backend, role, method):
    b = backend
    receipt, headers = accepted_order(b, role=role, registered=True)
    before = b.client.get(path(receipt))
    assert before.status_code == 200 and before.json()["payment_status"] == "unverified"
    assert before.json()["payment"] is None
    with b.sessions.begin() as db:
        db.get(ProductVariant, b.products[0].variants[0].product_variant_id).price = 999
        snapshot = dict(db.get(OrderDiscountSnapshot, UUID(receipt["order_id"])).calculation)
    response = confirm(b, receipt, headers, method=method)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["payment_status"] == "verified" and body["total"] == "145.00" and body["currency"] == "MXN"
    assert body["payment"]["amount"] == receipt["total"] and body["payment"]["method"] == method
    assert body["payment"]["verified_by_account_id"] == b.ids[role][0]
    assert body["payment"]["verified_at"] and UUID(body["payment"]["payment_id"])
    assert response.headers["cache-control"] == "no-store"
    assert b.client.get(path(receipt)).json() == body
    with b.sessions() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        assert order.status == "Confirmado" and order.total == Decimal("145")
        assert len(db.scalars(select(OrderStatusHistory)).all()) == 2
        assert db.get(OrderDiscountSnapshot, order.order_id).calculation == snapshot
        assert db.get(CustomerCoupon, b.coupon.customer_coupon_id).status == "available"
    customer = login(b, "customer")
    assert customer.status_code == 200
    personal = b.client.get(f'/api/auth/orders/{receipt["order_id"]}')
    assert personal.status_code == 200 and "verified_by_account_id" not in personal.text
    assert b.client.get(path(receipt)).status_code == 403


@pytest.mark.parametrize("state", ["Aceptado", "En preparación", "Listo para recoger", "Servido", "Entregado", "Completado"])
def test_accepted_and_attended_orders_can_be_paid_without_changing_state(backend, state):
    b = backend
    receipt, headers = accepted_order(b)
    with b.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = state
    assert b.client.get(path(receipt)).json()["payment_status"] == "unverified"
    response = confirm(b, receipt, headers)
    assert response.status_code == 201, response.text
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == state


@pytest.mark.parametrize("modality", ["pickup", "dine_in"])
def test_payment_and_delivery_are_independent_in_both_directions(backend, modality):
    b = backend
    first, headers = accepted_order(b, modality=modality)
    paid = confirm(b, first, headers)
    assert paid.status_code == 201
    assert advance(b, first, headers).status_code == 200
    if modality == "pickup":
        assert advance(b, first, headers, "ready").status_code == 200
    assert advance(b, first, headers, "deliver" if modality == "pickup" else "serve").status_code == 200
    assert advance(b, first, headers, "complete").status_code == 200
    assert b.client.get(path(first)).json() == paid.json()
    second, headers = accepted_order(b, modality=modality)
    assert advance(b, second, headers).status_code == 200
    if modality == "pickup":
        assert advance(b, second, headers, "ready").status_code == 200
    assert advance(b, second, headers, "deliver" if modality == "pickup" else "serve").status_code == 200
    assert advance(b, second, headers, "complete").status_code == 200
    assert b.client.get(path(second)).json()["payment_status"] == "unverified"
    assert confirm(b, second, headers).status_code == 201


@pytest.mark.parametrize("state", ["Solicitud recibida", "Cancelado", "Rechazado", "Estado desconocido"])
def test_unconfirmed_cancelled_and_rejected_requests_cannot_be_paid(backend, state):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b, "staff"))
    with b.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = state
    assert confirm(b, receipt, headers).status_code == 409
    assert b.client.get(path(receipt)).json()["payment_status"] == "unverified"
    with b.sessions() as db:
        assert db.scalar(select(OrderPayment)) is None


@pytest.mark.parametrize("amount", ["1.00", "170.0000000000000000000001", "999.00"])
def test_partial_excess_and_fractional_differences_do_not_round_into_a_payment(backend, amount):
    b = backend
    receipt, headers = accepted_order(b)
    assert confirm(b, receipt, headers, amount=amount).status_code == 409
    assert b.client.get(path(receipt)).json()["payment_status"] == "unverified"
    assert confirm(b, receipt, headers).status_code == 201


@pytest.mark.parametrize("body", [
    {}, {"amount": "170"}, {"amount": "0", "method": "cash"}, {"amount": "-1", "method": "cash"},
    {"amount": 170, "method": "cash"}, {"amount": 170.0, "method": "cash"},
    {"amount": True, "method": "cash"}, {"amount": "NaN", "method": "cash"},
    {"amount": "Infinity", "method": "cash"}, {"amount": "1e2", "method": "cash"},
    {"amount": " 170 ", "method": "cash"}, {"amount": "170", "method": "unknown"},
    {"amount": "170", "method": "cash", "verified_by_account_id": 999},
    {"amount": "170", "method": "cash", "verified_at": "2026-01-01T00:00:00Z"},
    {"amount": "170", "method": "cash", "status": "Completado"},
])
def test_payment_body_rejects_invalid_money_and_server_controlled_fields(backend, body):
    b = backend
    receipt, headers = accepted_order(b)
    response = b.client.post(path(receipt), headers=headers, json=body)
    assert response.status_code == 422, response.text
    with b.sessions() as db:
        assert db.scalar(select(OrderPayment)) is None


@pytest.mark.parametrize("role", [None, "customer"])
def test_guests_and_customers_cannot_read_or_confirm_payments(backend, role):
    b = backend
    receipt, _ = accepted_order(b)
    b.client.cookies.clear()
    headers = csrf_headers(login(b, role)) if role else {"Origin": ORIGIN}
    assert b.client.get(path(receipt)).status_code == (403 if role else 401)
    assert confirm(b, receipt, headers).status_code == (403 if role else 401)


def test_employee_activity_csrf_origin_and_dedicated_permissions_are_required(backend, monkeypatch):
    b = backend
    receipt, headers = accepted_order(b)
    assert confirm(b, receipt, {"Origin": ORIGIN}).status_code == 403
    assert confirm(b, receipt, headers | {"Origin": "https://evil.invalid"}).status_code == 403
    assert confirm(b, receipt, headers | {"X-CSRF-Token": "0" * 64}).status_code == 403
    monkeypatch.setattr(auth_service, "permissions_for_roles", lambda *args, **kwargs: frozenset({"catalog:read", "orders:read", "orders:write"}))
    headers = csrf_headers(login(b, "staff"))
    assert b.client.get(path(receipt)).status_code == 403
    assert confirm(b, receipt, headers).status_code == 403
    monkeypatch.setattr(auth_service, "permissions_for_roles", lambda *args, **kwargs: frozenset({"catalog:read", "orders:read", "orders:write", "payments:read"}))
    headers = csrf_headers(login(b, "staff"))
    assert b.client.get(path(receipt)).status_code == 200
    assert confirm(b, receipt, headers).status_code == 403
    with b.sessions.begin() as db:
        db.get(Employee, b.ids["staff"][1]).is_active = False
    assert b.client.get(path(receipt)).status_code == 401
    assert confirm(b, receipt, headers).status_code == 401


def test_unknown_orders_fail_without_creating_payments(backend):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    receipt = {"order_id": str(uuid4()), "total": "170.00"}
    assert b.client.get(path(receipt)).status_code == 404
    assert confirm(b, receipt, headers).status_code == 404


def test_missing_migration_returns_503_without_breaking_existing_order_routes(backend):
    b = backend
    receipt, headers = accepted_order(b)
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261121_0014")
    try:
        assert b.client.get(path(receipt)).status_code == 503
        assert confirm(b, receipt, headers).status_code == 503
        assert b.client.get(f'/api/admin/orders/{receipt["order_id"]}').status_code == 200
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261124_0015")


def test_zero_total_is_not_a_verified_payment(backend):
    b = backend
    with b.sessions.begin() as db:
        for product in b.products:
            db.get(ProductVariant, product.variants[0].product_variant_id).price = 0
    b.selection["cart"]["items"][0]["complement_ids"] = []
    receipt, headers = accepted_order(b)
    assert receipt["total"] == "0.00"
    read = b.client.get(path(receipt))
    assert read.status_code == 200 and read.json()["payment_status"] == "not_required"
    assert confirm(b, receipt, headers, amount="1").status_code == 409
    assert read.json()["payment"] is None


@pytest.mark.parametrize("fault", ["insert", "commit"])
def test_failed_transaction_rolls_back_and_retry_can_verify_without_leaking_sql(backend, caplog, fault):
    b = backend
    receipt, headers = accepted_order(b)
    engine = b.service.sessions.kw["bind"]
    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO "OrderPayments"'):
            raise OperationalError("private-payment-error", {}, Exception("private-payment-error"))
    def fail_commit(conn):
        raise OperationalError("private-payment-error", {}, Exception("private-payment-error"))
    hook, callback = ("before_cursor_execute", fail) if fault == "insert" else ("commit", fail_commit)
    event.listen(engine, hook, callback)
    try:
        failed = confirm(b, receipt, headers)
    finally:
        event.remove(engine, hook, callback)
    assert failed.status_code == 503 and "private-payment-error" not in failed.text + caplog.text
    assert b.client.get(path(receipt)).json()["payment_status"] == "unverified"
    assert confirm(b, receipt, headers).status_code == 201


def test_retries_and_concurrent_confirmation_preserve_one_original_record(backend):
    b = backend
    receipt, headers = accepted_order(b)
    barrier = Barrier(2)
    def save(role):
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                return order_payment_service.confirm_payment(db, UUID(receipt["order_id"]),
                    OrderPaymentInput(amount=Decimal(receipt["total"]), method="cash"), actor_account_id=b.ids[role][0])
        except AuthError as error:
            assert error.status_code == 409
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(save, role) for role in ("admin", "staff")]
        values = [future.result(timeout=15) for future in results]
    assert sum(value is not None for value in values) == 1
    original = b.client.get(path(receipt)).json()
    assert confirm(b, receipt, headers, method="card").status_code == 409
    assert b.client.get(path(receipt)).json() == original
    with b.sessions() as db:
        assert len(db.scalars(select(OrderPayment)).all()) == 1


def test_service_validates_input_before_using_a_database():
    db = Mock()
    with pytest.raises(AuthError) as error:
        order_payment_service.confirm_payment(db, uuid4(), {"amount": "0", "method": "cash"}, actor_account_id=1)
    assert error.value.status_code == 422 and db.mock_calls == []


def test_exact_high_precision_total_is_preserved_without_rounding(backend):
    b = backend
    with b.sessions.begin() as db:
        db.get(ProductVariant, b.products[0].variants[0].product_variant_id).price = Decimal("0.12345678901234567890123456789")
    receipt, headers = accepted_order(b)
    assert len(receipt["total"].partition(".")[2]) > 2
    response = confirm(b, receipt, headers)
    assert response.status_code == 201, response.text
    assert response.json()["payment"]["amount"] == receipt["total"]
    assert b.client.get(path(receipt)).json() == response.json()


def test_payment_routes_keep_private_cors_and_guest_tracking_boundaries(backend):
    b = backend
    receipt, headers = accepted_order(b)
    preflight = b.client.options(path(receipt), headers={"Origin": ORIGIN,
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token"})
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"
    assert b.client.options(path(receipt), headers={"Origin": "https://evil.invalid",
        "Access-Control-Request-Method": "POST"}).status_code == 400
    paid = confirm(b, receipt, headers)
    assert paid.status_code == 201
    assert set(paid.json()) == {"order_id", "currency", "total", "payment_status", "payment"}
    b.client.cookies.clear()
    tracked = b.client.get(receipt["tracking"]["path"], headers={"Authorization": "Bearer " + receipt["tracking"]["token"]})
    assert tracked.status_code == 200 and tracked.json()["status"] == "Confirmado"
    assert "verified_by_account_id" not in tracked.text and "payment_id" not in tracked.text
    assert b.client.get(path(receipt), headers={"Authorization": "Bearer " + receipt["tracking"]["token"]}).status_code == 401


def test_confirmation_rechecks_the_state_after_waiting_for_the_order_lock(backend):
    b = backend
    receipt, headers = accepted_order(b)
    waiting = Event()
    engine = b.service.sessions.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, many):
        if 'FROM "Orders"' in statement and "FOR UPDATE" in statement:
            waiting.set()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with b.sessions.begin() as db:
                order = db.scalar(select(Order).where(Order.order_id == UUID(receipt["order_id"])).with_for_update())
                future = pool.submit(confirm, b, receipt, headers)
                assert waiting.wait(10)
                order.status = "Cancelado"
            response = future.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 409, response.text
    assert b.client.get(path(receipt)).json()["payment_status"] == "unverified"

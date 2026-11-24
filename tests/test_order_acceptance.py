"""HU-025: confirma contra el catálogo local sin reescribir la cotización histórica."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select

from app.models import CustomerCoupon, Discount, Order, OrderStatusHistory, Product, ProductComplement, ProductVariant
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY
from test_staff_order_management import (
    auth_backend, auth_database, backend, coupon_backend, csrf_headers, decide,
    guest_order, login, password_hash,
)
from test_commercial_discounts_postgresql import add_discount


def pending(b, *, coupon=False):
    """Crea la solicitud como cliente cuando incluye un cupón; luego cambia al personal."""
    if coupon:
        headers = csrf_headers(login(b, "customer"))
        response = b.client.post("/api/orders", headers=headers | {"Idempotency-Key": uuid4().hex},
            json={"cart": b.selection["cart"], "customer_coupon_id": b.coupon.customer_coupon_id,
                  "data": {"modality": "pickup", "name": "Cliente", "phone": "+522281234567"}})
        assert response.status_code == 201, response.text
        receipt = response.json()
    else:
        receipt = guest_order(b)
    return receipt, csrf_headers(login(b, "staff"))


def assert_pending(b, receipt):
    with b.sessions() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        assert order.status == "Solicitud recibida"
        assert str(order.total) == receipt["total"]
        assert len(db.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.order_id)).all()) == 1


@pytest.mark.parametrize("change", ["price", "extra", "offsetting", "product_active", "product_available",
                                    "variant_available", "commercial_available", "extra_active"])
def test_catalog_changes_block_confirmation_without_repricing(backend, change):
    b = backend
    receipt, headers = pending(b)
    with b.sessions.begin() as db:
        product = db.get(Product, b.products[0].product_id)
        variant = db.get(ProductVariant, b.products[0].variants[0].product_variant_id)
        extra = db.get(ProductComplement, b.complement.product_complement_id)
        if change in {"price", "offsetting"}:
            variant.price = 55
        if change == "extra":
            extra.price = 10
        if change == "offsetting":
            extra.price = 0
        if change == "product_active":
            product.is_active = False
        if change == "product_available":
            product.is_available = False
        if change == "variant_available":
            variant.is_available = False
        if change == "commercial_available":
            variant.is_commercially_available = False
        if change == "extra_active":
            extra.is_active = False
    response = decide(b, receipt, headers)
    assert response.status_code == 409, response.text
    assert_pending(b, receipt)


def test_price_recovery_allows_retry_and_names_do_not_reprice_history(backend):
    b = backend
    receipt, headers = pending(b)
    with b.sessions.begin() as db:
        db.get(ProductVariant, b.products[0].variants[0].product_variant_id).price = 99
    assert decide(b, receipt, headers).status_code == 409
    with b.sessions.begin() as db:
        variant = db.get(ProductVariant, b.products[0].variants[0].product_variant_id)
        variant.price, variant.name = 50, "Nombre nuevo"
        db.get(Product, b.products[0].product_id).name = "Otro nombre"
        db.get(ProductComplement, b.complement.product_complement_id).name = "Otro extra"
    response = decide(b, receipt, headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "Confirmado"
    assert response.json()["items"] == receipt["items"]


@pytest.mark.parametrize("change", ["expired", "reserved", "redeemed", "conditions", "discount_inactive",
                                    "discount_value", "owner"])
def test_selected_coupon_is_revalidated_for_its_owner(backend, change):
    b = backend
    receipt, headers = pending(b, coupon=True)
    with b.sessions.begin() as db:
        coupon = db.get(CustomerCoupon, b.coupon.customer_coupon_id)
        discount = db.get(Discount, b.discount.discount_id)
        if change == "expired":
            coupon.expires_at = b.now.value - timedelta(seconds=1)
        elif change in {"reserved", "redeemed"}:
            coupon.status = change
        elif change == "conditions":
            coupon.conditions = coupon.conditions | {"minimum_subtotal": "999"}
        elif change == "discount_inactive":
            discount.is_active = False
        elif change == "discount_value":
            discount.value = 20
        elif change == "owner":
            coupon.account_id = b.ids["admin"][0]
    response = decide(b, receipt, headers)
    assert response.status_code == 409, response.text
    assert_pending(b, receipt)


def test_unapplied_discount_metadata_does_not_block_confirmation(backend):
    b = backend
    receipt, headers = pending(b)
    with b.sessions.begin() as db:
        db.get(Discount, b.discount.discount_id).name = "Nombre actualizado sin reglas aplicables"
    response = decide(b, receipt, headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("change", ["value", "inactive", "new_discount", "name_only"])
def test_applied_commercial_benefits_are_compared_against_the_snapshot(backend, change):
    b = backend
    discount = add_discount(b.sessions, [b.products[0].product_id])
    receipt, headers = pending(b)
    assert receipt["applied_discounts"]
    if change == "new_discount":
        add_discount(b.sessions, [b.products[1].product_id])
    else:
        with b.sessions.begin() as db:
            row = db.get(Discount, discount.discount_id)
            if change == "value":
                row.value = 20
            elif change == "inactive":
                row.is_active = False
            else:
                row.name = "Etiqueta nueva"
    response = decide(b, receipt, headers)
    assert response.status_code == (200 if change == "name_only" else 409), response.text
    if change != "name_only":
        assert_pending(b, receipt)


def test_busy_catalog_can_be_retried_without_changing_the_order(backend):
    b = backend
    receipt, headers = pending(b)
    with b.sessions.begin() as db:
        db.scalar(select(func.pg_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY)))
        response = decide(b, receipt, headers)
        assert response.status_code == 409, response.text
        assert_pending(b, receipt)
    assert decide(b, receipt, headers).status_code == 200


def test_catalog_lock_is_held_through_state_and_history_writes(backend):
    b = backend
    receipt, headers = pending(b)
    observed = []
    engine = b.service.sessions.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('UPDATE "Orders"') or statement.startswith('INSERT INTO "OrderStatusHistories"'):
            with b.sessions.begin() as other:
                observed.append(other.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))))
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = decide(b, receipt, headers)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert observed == [False, False]
    with b.sessions.begin() as db:
        assert db.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY)))


def test_coupon_clock_is_evaluated_after_waiting_for_the_order_lock(backend):
    b = backend
    receipt, headers = pending(b, coupon=True)
    with b.sessions.begin() as db:
        db.get(CustomerCoupon, b.coupon.customer_coupon_id).expires_at = b.now.value + timedelta(minutes=1)
    waiting = Event()
    engine = b.service.sessions.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, executemany):
        if 'FROM "Orders"' in statement and "FOR UPDATE" in statement:
            waiting.set()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with b.sessions.begin() as db:
                db.scalar(select(Order).where(Order.order_id == UUID(receipt["order_id"])).with_for_update())
                future = pool.submit(decide, b, receipt, headers)
                assert waiting.wait(10)
                b.now.value += timedelta(minutes=2)
            response = future.result(timeout=15)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 409, response.text
    assert_pending(b, receipt)


def test_preparation_preserves_legacy_accepted_events(backend):
    b = backend
    receipt, headers = pending(b)
    assert decide(b, receipt, headers).status_code == 200
    with b.sessions.begin() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        order.status = "Aceptado"
        history = db.scalar(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.order_id,
                                                            OrderStatusHistory.status == "Confirmado"))
        history.status = "Aceptado"
    response = b.client.post(f'/api/admin/orders/{receipt["order_id"]}/prepare', headers=headers, json={})
    assert response.status_code == 200, response.text
    assert [row["status"] for row in response.json()["history"]] == ["Solicitud recibida", "Aceptado", "En preparación"]
    assert response.json()["history"][-1]["previous_status"] == "Aceptado"

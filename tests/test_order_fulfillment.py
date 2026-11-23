"""Servido, entrega y finalización manuales, con historial y concurrencia reales."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, Order, OrderStatusHistory, ProductVariant, RestaurantOrderSettings
from app.services import staff_order_service
from test_auth_api import csrf_headers, login, ORIGIN
from test_order_preparation import (
    accepted_order, advance, backend, coupon_backend, auth_backend, auth_database, password_hash,
)


PATH = "/api/admin/orders"


def prepared_order(b, *, modality="pickup", role="staff", registered=False):
    """Recorre las acciones reales hasta En preparación o Listo para recoger."""
    receipt, headers = accepted_order(b, modality=modality, role=role, registered=registered)
    assert advance(b, receipt, headers).status_code == 200
    if modality == "pickup":
        assert advance(b, receipt, headers, "ready").status_code == 200
    return receipt, headers


def source_for_action(b, action):
    """Prepara el estado de origen válido de cada acción, sin saltarse pasos por SQL."""
    receipt, headers = prepared_order(b, modality="pickup" if action == "deliver" else "dine_in")
    if action == "complete":
        assert advance(b, receipt, headers, "serve").status_code == 200
    return receipt, headers


def history(b, identifier):
    """Lee los eventos persistidos con una conexión distinta de la petición HTTP."""
    with b.sessions() as db:
        return db.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == UUID(identifier))
                          .order_by(OrderStatusHistory.created_at)).all()


@pytest.mark.parametrize("role", ["admin", "staff"])
@pytest.mark.parametrize("modality", ["dine_in", "pickup"])
def test_explicit_handoff_and_completion_preserve_history_and_amounts(backend, role, modality):
    b = backend
    receipt, headers = prepared_order(b, modality=modality, role=role)
    with b.sessions.begin() as db:
        db.get(ProductVariant, b.products[0].variants[0].product_variant_id).price = 999
        db.get(RestaurantOrderSettings, 1).accepting_orders = False
    action, state = ("serve", "Servido") if modality == "dine_in" else ("deliver", "Entregado")
    handed = advance(b, receipt, headers, action)
    assert handed.status_code == 200 and handed.json()["status"] == state, handed.text
    assert advance(b, receipt, headers, action).status_code == 409
    assert handed.headers["cache-control"] == "no-store"
    for field in ("items", "subtotal", "discount_total", "total", "coupon"):
        assert handed.json()[field] == receipt[field]
    completed = advance(b, receipt, headers, "complete")
    assert completed.status_code == 200 and completed.json()["status"] == "Completado", completed.text
    assert advance(b, receipt, headers, "complete").status_code == 409
    states = ["Solicitud recibida", "Aceptado", "En preparación"]
    if modality == "pickup":
        states.append("Listo para recoger")
    states += [state, "Completado"]
    events = history(b, receipt["order_id"])
    assert [row.status for row in events] == states
    assert [row.previous_status for row in events] == [None, *states[:-1]]
    assert all(row.actor_account_id == b.ids[role][0] for row in events[1:])
    with b.sessions() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        assert order.total == Decimal(receipt["total"])
    b.client.cookies.clear()
    tracked = b.client.get(receipt["tracking"]["path"], headers={"Authorization": "Bearer " + receipt["tracking"]["token"]})
    assert tracked.status_code == 200 and tracked.json()["status"] == "Completado"
    assert tracked.json()["items"] == receipt["items"] and tracked.json()["total"] == receipt["total"]


@pytest.mark.parametrize("action,modality", [("serve", "pickup"), ("deliver", "dine_in")])
def test_handoff_rejects_the_other_modality(backend, action, modality):
    b = backend
    receipt, headers = prepared_order(b, modality=modality)
    before = [row.status for row in history(b, receipt["order_id"])]
    assert advance(b, receipt, headers, action).status_code == 409
    assert [row.status for row in history(b, receipt["order_id"])] == before


@pytest.mark.parametrize("action,state", [
    ("serve", "Solicitud recibida"), ("serve", "Aceptado"), ("serve", "Rechazado"),
    ("deliver", "Aceptado"), ("deliver", "En preparación"), ("deliver", "Cancelado"),
    ("complete", "Solicitud recibida"), ("complete", "Aceptado"), ("complete", "En preparación"),
    ("complete", "Listo para recoger"), ("complete", "Cancelado"), ("complete", "Rechazado"),
    ("complete", "Completado"),
])
def test_actions_cannot_skip_or_reverse_the_flow(backend, action, state):
    b = backend
    receipt, headers = source_for_action(b, action)
    before = len(history(b, receipt["order_id"]))
    with b.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = state
    assert advance(b, receipt, headers, action).status_code == 409
    assert len(history(b, receipt["order_id"])) == before
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == state


@pytest.mark.parametrize("modality,state", [("dine_in", "Entregado"), ("pickup", "Servido")])
def test_completion_requires_the_handoff_state_for_its_modality(backend, modality, state):
    b = backend
    receipt, headers = prepared_order(b, modality=modality)
    with b.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = state
    assert advance(b, receipt, headers, "complete").status_code == 409


@pytest.mark.parametrize("action", ["serve", "deliver", "complete"])
@pytest.mark.parametrize("role", [None, "customer"])
def test_guests_and_customers_have_no_operational_permission(backend, action, role):
    b = backend
    receipt, _ = source_for_action(b, action)
    b.client.cookies.clear()
    headers = csrf_headers(login(b, role)) if role else {"Origin": ORIGIN}
    assert advance(b, receipt, headers, action).status_code == (401 if role is None else 403)


@pytest.mark.parametrize("action", ["serve", "deliver", "complete"])
@pytest.mark.parametrize("fault", ["csrf", "origin"])
def test_staff_actions_require_csrf_and_origin(backend, action, fault):
    b = backend
    receipt, headers = source_for_action(b, action)
    headers.pop("X-CSRF-Token" if fault == "csrf" else "Origin")
    assert advance(b, receipt, headers, action).status_code == 403


@pytest.mark.parametrize("action", ["serve", "deliver", "complete"])
@pytest.mark.parametrize("body", [{"status": "Completado"}, {"paid": True}, {"actor_account_id": 1}])
def test_only_empty_confirmation_body_is_accepted(backend, action, body):
    b = backend
    receipt, headers = source_for_action(b, action)
    assert advance(b, receipt, headers, action, body).status_code == 422


@pytest.mark.parametrize("action", ["serve", "deliver", "complete"])
def test_unknown_orders_return_404(backend, action):
    b = backend
    headers = csrf_headers(login(b, "staff"))
    assert advance(b, {"order_id": str(uuid4())}, headers, action).status_code == 404


@pytest.mark.parametrize("action", ["serve", "deliver", "complete"])
def test_history_failure_rolls_back_handoff_or_completion(backend, action):
    b = backend
    receipt, headers = source_for_action(b, action)
    previous_events = history(b, receipt["order_id"])
    previous = previous_events[-1].status
    engine = b.service.sessions.kw["bind"]
    def fail(conn, cursor, statement, parameters, context, many):
        """Falla el historial después de actualizar el pedido, para verificar atomicidad."""
        if 'INSERT INTO "OrderStatusHistories"' in statement:
            raise OperationalError("synthetic", {}, Exception("private-password"))
    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = advance(b, receipt, headers, action)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert response.status_code == 503 and "private-password" not in response.text
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == previous
    assert len(history(b, receipt["order_id"])) == len(previous_events)
    assert advance(b, receipt, headers, action).status_code == 200


@pytest.mark.parametrize("action", ["serve", "deliver", "complete"])
def test_concurrent_duplicate_confirmations_have_one_winner(backend, action):
    b = backend
    receipt, _ = source_for_action(b, action)
    count = len(history(b, receipt["order_id"]))
    function = {"serve": staff_order_service.mark_served, "deliver": staff_order_service.mark_delivered,
                "complete": staff_order_service.complete_order}[action]
    barrier = Barrier(2)
    def execute(role):
        """Cada empleado usa una transacción independiente sobre el mismo pedido."""
        try:
            with b.sessions() as db, db.begin():
                barrier.wait(timeout=5)
                function(db, UUID(receipt["order_id"]), actor_account_id=b.ids[role][0])
            return "success"
        except AuthError as error:
            assert error.status_code == 409
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, ["admin", "staff"]))
    assert sorted(results) == ["conflict", "success"]
    assert len(history(b, receipt["order_id"])) == count + 1


@pytest.mark.parametrize("modality", ["dine_in", "pickup"])
def test_customer_sees_completion_with_original_coupon_without_consumption(backend, modality):
    b = backend
    receipt, headers = prepared_order(b, modality=modality, registered=True)
    action = "serve" if modality == "dine_in" else "deliver"
    assert advance(b, receipt, headers, action).status_code == 200
    assert advance(b, receipt, headers, "complete").status_code == 200
    customer = csrf_headers(login(b, "customer"))
    detail = b.client.get(f'/api/auth/orders/{receipt["order_id"]}')
    assert detail.status_code == 200 and detail.json()["status"] == "Completado"
    assert detail.json()["total"] == receipt["total"] and detail.json()["coupon"] == receipt["coupon"]
    assert detail.json()["can_cancel"] is False
    assert b.client.post(f'/api/orders/{receipt["order_id"]}/cancel', headers=customer, json={}).status_code == 409
    with b.sessions() as db:
        assert db.get(CustomerCoupon, b.coupon.customer_coupon_id).status == "available"

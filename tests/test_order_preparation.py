"""Preparación por modalidad, historial atómico y concurrencia en PostgreSQL temporal."""

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
from test_staff_order_management import backend, coupon_backend, auth_backend, auth_database, password_hash


PATH = "/api/admin/orders"


def accepted_order(b, *, modality="pickup", role="staff", registered=False):
    """Registra y acepta mediante las rutas reales; devuelve recibo y credencial CSRF del personal."""
    headers = csrf_headers(login(b, "customer")) if registered else {}
    if not registered:
        b.client.cookies.clear()
    data = {"modality": modality, "name": "Solicitante sintético"}
    if modality == "pickup":
        data["phone"] = "+522281234567"
    payload = {"cart": b.selection["cart"], "data": data}
    if registered:
        payload["customer_coupon_id"] = b.coupon.customer_coupon_id
    receipt = b.client.post("/api/orders", headers=headers | {"Idempotency-Key": str(uuid4())}, json=payload)
    assert receipt.status_code == 201, receipt.text
    headers = csrf_headers(login(b, role))
    response = b.client.post(f'{PATH}/{receipt.json()["order_id"]}/accept', headers=headers, json={})
    assert response.status_code == 200, response.text
    return receipt.json(), headers


def advance(b, receipt, headers, action="prepare", body=None):
    """Invoca una acción explícita; el payload no puede escoger el estado de destino."""
    return b.client.post(f'{PATH}/{receipt["order_id"]}/{action}', headers=headers, json={} if body is None else body)


@pytest.mark.parametrize("role", ["admin", "staff"])
@pytest.mark.parametrize("modality", ["dine_in", "pickup"])
def test_preparation_and_pickup_readiness_preserve_history_and_prices(backend, role, modality):
    b = backend
    receipt, headers = accepted_order(b, modality=modality, role=role)
    with b.sessions.begin() as db:
        db.get(ProductVariant, b.products[0].variants[0].product_variant_id).price = 999
        db.get(RestaurantOrderSettings, 1).accepting_orders = False
    prepared = advance(b, receipt, headers)
    assert prepared.status_code == 200 and prepared.json()["status"] == "En preparación", prepared.text
    assert prepared.json()["items"] == receipt["items"] and prepared.json()["total"] == receipt["total"]
    assert prepared.headers["cache-control"] == "no-store"
    assert advance(b, receipt, headers).status_code == 409
    ready = advance(b, receipt, headers, "ready")
    expected = "Listo para recoger" if modality == "pickup" else "En preparación"
    assert ready.status_code == (200 if modality == "pickup" else 409)
    if modality == "pickup":
        assert ready.json()["status"] == expected
        assert advance(b, receipt, headers, "ready").status_code == 409
    with b.sessions() as db:
        row = db.get(Order, UUID(receipt["order_id"]))
        assert row.status == expected and row.total == Decimal(receipt["total"])
        history = db.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == row.order_id)
                             .order_by(OrderStatusHistory.created_at)).all()
        states = ["Solicitud recibida", "Confirmado", "En preparación"]
        if modality == "pickup":
            states.append("Listo para recoger")
        assert [state.status for state in history] == states
        assert [state.previous_status for state in history] == [None, *states[:-1]]
        assert all(state.actor_account_id == b.ids[role][0] for state in history[1:])
        assert db.get(CustomerCoupon, b.coupon.customer_coupon_id).status == "available"
    b.client.cookies.clear()
    tracked = b.client.get(receipt["tracking"]["path"], headers={"Authorization": "Bearer " + receipt["tracking"]["token"]})
    assert tracked.status_code == 200 and tracked.json()["status"] == expected
    assert tracked.json()["items"] == receipt["items"] and tracked.json()["total"] == receipt["total"]


@pytest.mark.parametrize("action,state", [
    ("prepare", "Solicitud recibida"), ("prepare", "Rechazado"), ("prepare", "Cancelado"),
    ("prepare", "Listo para recoger"), ("prepare", "Completado"),
    ("ready", "Solicitud recibida"), ("ready", "Aceptado"), ("ready", "Cancelado"),
    ("ready", "Entregado"), ("ready", "Completado"),
])
def test_actions_cannot_skip_or_reverse_states(backend, action, state):
    b = backend
    receipt, headers = accepted_order(b)
    with b.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = state
    assert advance(b, receipt, headers, action).status_code == 409
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == state
        assert len(db.scalars(select(OrderStatusHistory)).all()) == 2


@pytest.mark.parametrize("action", ["prepare", "ready"])
@pytest.mark.parametrize("role", [None, "customer"])
def test_guests_and_customers_cannot_advance_orders(backend, action, role):
    b = backend
    receipt, _ = accepted_order(b)
    b.client.cookies.clear()
    headers = csrf_headers(login(b, role)) if role else {"Origin": ORIGIN}
    assert advance(b, receipt, headers, action).status_code == (401 if role is None else 403)


@pytest.mark.parametrize("action", ["prepare", "ready"])
@pytest.mark.parametrize("fault", ["csrf", "origin"])
def test_staff_actions_require_csrf_and_origin(backend, action, fault):
    b = backend
    receipt, headers = accepted_order(b)
    headers.pop("X-CSRF-Token" if fault == "csrf" else "Origin")
    assert advance(b, receipt, headers, action).status_code == 403


@pytest.mark.parametrize("action", ["prepare", "ready"])
@pytest.mark.parametrize("body", [{"status": "Completado"}, {"modality": "pickup"}, {"actor_account_id": 1}])
def test_actions_reject_client_supplied_state_or_actor(backend, action, body):
    b = backend
    receipt, headers = accepted_order(b)
    assert advance(b, receipt, headers, action, body).status_code == 422


@pytest.mark.parametrize("action", ["prepare", "ready"])
def test_unknown_orders_return_404(backend, action):
    b = backend
    headers = csrf_headers(login(b, "staff"))
    assert advance(b, {"order_id": str(uuid4())}, headers, action).status_code == 404


@pytest.mark.parametrize("action", ["prepare", "ready"])
def test_history_failure_rolls_back_the_transition(backend, action):
    b = backend
    receipt, headers = accepted_order(b)
    if action == "ready":
        assert advance(b, receipt, headers).status_code == 200
    previous = "Confirmado" if action == "prepare" else "En preparación"
    engine = b.service.sessions.kw["bind"]
    def fail(conn, cursor, statement, parameters, context, many):
        """Falla después del UPDATE para comprobar su rollback junto con el historial."""
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
        assert len(db.scalars(select(OrderStatusHistory)).all()) == (2 if action == "prepare" else 3)
    assert advance(b, receipt, headers, action).status_code == 200


@pytest.mark.parametrize("action", ["prepare", "ready"])
def test_concurrent_duplicate_actions_record_only_one_transition(backend, action):
    b = backend
    receipt, headers = accepted_order(b)
    if action == "ready":
        assert advance(b, receipt, headers).status_code == 200
    barrier = Barrier(2)
    function = staff_order_service.start_preparation if action == "prepare" else staff_order_service.mark_ready_for_pickup
    def execute(role):
        """Usa conexiones independientes contra el mismo pedido ya confirmado."""
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
    with b.sessions() as db:
        assert len(db.scalars(select(OrderStatusHistory)).all()) == (3 if action == "prepare" else 4)


def test_registered_customer_sees_preparation_without_gaining_staff_actions(backend):
    b = backend
    receipt, headers = accepted_order(b, registered=True)
    assert advance(b, receipt, headers).status_code == 200
    assert advance(b, receipt, headers, "ready").status_code == 200
    customer = csrf_headers(login(b, "customer"))
    detail = b.client.get(f'/api/auth/orders/{receipt["order_id"]}')
    assert detail.status_code == 200 and detail.json()["status"] == "Listo para recoger"
    assert detail.json()["can_cancel"] is False and detail.json()["total"] == receipt["total"]
    assert detail.json()["coupon"] == receipt["coupon"]
    assert b.client.post(f'/api/orders/{receipt["order_id"]}/cancel', headers=customer, json={}).status_code == 409

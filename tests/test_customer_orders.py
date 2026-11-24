"""Historial propio en PostgreSQL temporal: propiedad, importes y privacidad."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.models import CustomerCoupon, Discount, Order, Product, ProductVariant, RestaurantOrderSettings
from test_auth_api import csrf_headers, login, ORIGIN
from test_coupon_selection import backend, auth_backend, auth_database, password_hash


PATH = "/api/auth/orders"


def create_order(b, role="customer", *, coupon=False):
    if role is None:
        b.client.cookies.clear()
        headers = {}
    else:
        headers = csrf_headers(login(b, role))
    body = {"cart": b.selection["cart"],
            "data": {"modality": "pickup", "name": "Contacto privado", "phone": "+522281234567"}}
    if coupon:
        body["customer_coupon_id"] = b.coupon.customer_coupon_id
    response = b.client.post("/api/orders", headers=headers | {"Idempotency-Key": str(uuid4())}, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_empty_history_is_paginated_and_not_cached(backend):
    b = backend
    login(b, "customer")
    response = b.client.get(PATH)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 25, "offset": 0}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("role", ["customer", "admin", "staff"])
def test_each_account_lists_only_its_own_orders_even_with_admin_roles(backend, role):
    b = backend
    own = create_order(b, role)
    foreign = create_order(b, "staff" if role != "staff" else "admin")
    guest = create_order(b, None)
    login(b, role)
    # Un account_id enviado por el cliente no cambia el alcance de su sesión.
    response = b.client.get(PATH, params={"account_id": b.ids["staff"][0]})
    assert response.status_code == 200 and response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["order_id"] == own["order_id"] and item["can_cancel"] is True
    assert item["total"] == own["total"] and item["currency"] == "MXN"
    assert foreign["order_id"] not in response.text and guest["order_id"] not in response.text
    assert "Contacto privado" not in response.text and "+522281234567" not in response.text


@pytest.mark.parametrize("role", ["customer", "admin", "staff"])
def test_foreign_guest_and_unknown_orders_share_the_same_404(backend, role):
    b = backend
    own = create_order(b, role)
    foreign = create_order(b, "staff" if role != "staff" else "admin")
    guest = create_order(b, None)
    login(b, role)
    assert b.client.get(f'{PATH}/{own["order_id"]}').status_code == 200
    failures = [b.client.get(f"{PATH}/{identifier}", headers={
        "Authorization": "Bearer " + guest["tracking"]["token"],
    }) for identifier in (foreign["order_id"], guest["order_id"], str(uuid4()))]
    assert all(r.status_code == 404 and r.json() == {"detail": "Pedido no encontrado."} for r in failures)


def test_pagination_filter_and_uuid_tie_break_are_stable(backend):
    b = backend
    receipts = [create_order(b) for _ in range(3)]
    identifiers = [UUID(row["order_id"]) for row in receipts]
    with b.sessions.begin() as db:
        for identifier in identifiers:
            db.get(Order, identifier).created_at = b.now.value
        db.get(Order, identifiers[0]).status = "Cancelado"
    expected = [str(identifier) for identifier in sorted(identifiers, reverse=True)]
    first = b.client.get(PATH, params={"limit": 2}).json()
    last = b.client.get(PATH, params={"limit": 2, "offset": 2}).json()
    assert first["total"] == last["total"] == 3
    assert [row["order_id"] for row in first["items"] + last["items"]] == expected
    assert b.client.get(PATH, params={"offset": 20}).json()["items"] == []
    cancelled = b.client.get(PATH, params={"status": "Cancelado"}).json()
    assert cancelled["total"] == 1 and cancelled["items"][0]["order_id"] == str(identifiers[0])
    assert cancelled["items"][0]["can_cancel"] is False
    assert b.client.get(PATH, params={"status": "No existe"}).json()["total"] == 0


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1},
                                    {"limit": "invalid"}, {"status": ""}, {"status": "x" * 51}])
def test_invalid_pagination_and_filter_return_safe_422(backend, params):
    b = backend
    login(b, "customer")
    assert b.client.get(PATH, params=params).status_code == 422


@pytest.mark.parametrize("coupon", [False, True])
def test_detail_preserves_historical_prices_and_benefits_without_private_fields(backend, coupon):
    b = backend
    receipt = create_order(b, coupon=coupon)
    with b.sessions.begin() as db:
        db.get(Product, b.products[0].product_id).name = "Nombre posterior"
        db.get(ProductVariant, b.products[0].variants[0].product_variant_id).price = 999
        db.get(CustomerCoupon, b.coupon.customer_coupon_id).expires_at = b.now.value - timedelta(seconds=1)
        db.get(Discount, b.discount.discount_id).value = 80
        db.get(RestaurantOrderSettings, 1).accepting_orders = False
    statements = []
    engine = b.service.sessions.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = b.client.get(f'{PATH}/{receipt["order_id"]}')
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    body = response.json()
    for field in ("items", "subtotal", "discount_total", "total", "applied_discounts", "pending_discounts", "coupon"):
        assert body[field] == receipt[field]
    assert body["can_cancel"] is True
    assert set(body) == {"order_id", "status", "created_at", "modality", "currency", "items", "subtotal",
                         "discount_total", "total", "discounts_status", "applied_discounts", "pending_discounts",
                         "coupon", "can_cancel"}
    assert "Contacto privado" not in response.text and "+522281234567" not in response.text
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    assert not any(field in sql for sql in statements for field in (
        "Orders.guest_tracking_token", '"Orders".guest_tracking_token', '"Orders".request_hash',
        '"Orders".requester_phone', '"Orders".requester_name',
    ))


@pytest.mark.parametrize("state", ["Confirmado", "Aceptado", "En preparación", "Rechazado", "Cancelado", "Completado"])
def test_current_state_controls_cancel_flag_and_existing_cancellation_revalidates(backend, state):
    b = backend
    receipt = create_order(b)
    headers = csrf_headers(login(b, "customer"))
    with b.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = state
    response = b.client.get(f'{PATH}/{receipt["order_id"]}')
    assert response.json()["status"] == state and response.json()["can_cancel"] is False
    assert b.client.post(f'/api/orders/{receipt["order_id"]}/cancel', json={}, headers=headers).status_code == 409


def test_pending_order_can_be_cancelled_and_history_remains_readable(backend):
    b = backend
    receipt = create_order(b)
    headers = csrf_headers(login(b, "customer"))
    assert b.client.get(f'{PATH}/{receipt["order_id"]}').json()["can_cancel"] is True
    assert b.client.post(f'/api/orders/{receipt["order_id"]}/cancel', json={}, headers=headers).status_code == 200
    detail = b.client.get(f'{PATH}/{receipt["order_id"]}').json()
    assert detail["status"] == "Cancelado" and detail["can_cancel"] is False
    assert detail["items"] == receipt["items"] and detail["total"] == receipt["total"]


@pytest.mark.parametrize("fault", ["missing", "expired", "revoked"])
def test_session_is_required_and_guest_token_is_not_a_substitute(backend, fault):
    b = backend
    receipt = create_order(b)
    headers = csrf_headers(login(b, "customer"))
    if fault == "missing":
        b.client.cookies.clear()
    elif fault == "expired":
        b.now.value += timedelta(hours=9)
    else:
        assert b.client.post("/api/auth/logout", json={}, headers=headers).status_code == 204
    guest_headers = {"Authorization": "Bearer " + "x" * 43}
    for path in (PATH, f'{PATH}/{receipt["order_id"]}'):
        assert b.client.get(path, headers=guest_headers).status_code == 401


def test_database_errors_are_sanitized(backend):
    b = backend
    receipt = create_order(b)
    engine = b.service.sessions.kw["bind"]
    def fail(conn, cursor, statement, parameters, context, many):
        if 'FROM "Orders"' in statement:
            raise OperationalError("synthetic", {}, Exception("private-password"))
    event.listen(engine, "before_cursor_execute", fail)
    try:
        responses = [b.client.get(path) for path in (PATH, f'{PATH}/{receipt["order_id"]}')]
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert all(r.status_code == 503 and "private-password" not in r.text for r in responses)

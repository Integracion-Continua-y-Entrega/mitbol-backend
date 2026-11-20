"""HU-023: propiedad, estados y concurrencia en PostgreSQL desechable."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError

from app.core.auth_http import cookie_name
from app.core.config import Settings
from app.main import create_app
from app.models import Order, ProductVariant, RestaurantOrderSettings
from app.schemas.orders import OrderSubmissionInput
from app.services import order_cancellation_service, order_submission_service
from test_auth_api import csrf_headers, login, ORIGIN
from test_auth_phase2_core import config as auth_config
from test_cart import item
from test_menu import add_product, detail_catalog
from test_order_submission import (
    auth_backend, auth_database, committed_orders, counts, menu_db, order_client, password_hash,
    payload, registered_order_backend, submit,
)
from test_order_tracking import authorization


def path(receipt):
    return f'/api/orders/{receipt["order_id"]}/cancel'


def cancel(client, receipt, *, headers=None):
    return client.post(path(receipt), json={}, headers=headers or {})


@pytest.mark.parametrize("data", [
    {"modality": "dine_in", "name": "Alias"},
    {"modality": "pickup", "name": "Nombre privado", "phone": "2281234567"},
])
def test_guest_cancels_own_pending_order_and_history_remains_available(order_client, menu_db, detail_catalog, data):
    key, body = uuid4(), payload(detail_catalog, data=data)
    receipt = submit(order_client, body, key=key).json()
    access = receipt["tracking"]
    headers = authorization(access["token"])
    before = order_client.get(access["path"], headers=headers).json()
    with menu_db.sessions.begin() as db:
        db.get(RestaurantOrderSettings, 1).accepting_orders = False
        db.get(ProductVariant, detail_catalog.product.variants[0].product_variant_id).is_available = False
    response = cancel(order_client, receipt, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"order_id": receipt["order_id"], "status": "Cancelado"}
    assert response.headers["cache-control"] == "no-store"
    assert access["token"] not in response.text and data["name"] not in response.text
    after = order_client.get(access["path"], headers=headers)
    assert after.status_code == 200 and after.json() == {**before, "status": "Cancelado"}
    replay = submit(order_client, body, key=key)
    assert replay.status_code == 201 and replay.json() == {**receipt, "status": "Cancelado"}
    assert cancel(order_client, receipt, headers=headers).status_code == 409
    assert counts(menu_db.sessions) == (1, 2, 1)


@pytest.mark.parametrize("access", ["missing", "malformed", "other_order", "identifier", "idempotency_key"])
def test_guest_cannot_cancel_without_private_authorization(order_client, menu_db, detail_catalog, access):
    key = uuid4()
    first = submit(order_client, payload(detail_catalog), key=key).json()
    other = submit(order_client, payload(detail_catalog)).json()
    headers = {
        "missing": {}, "malformed": authorization("invalid"),
        "other_order": authorization(other["tracking"]["token"]),
        "identifier": authorization(first["order_id"]), "idempotency_key": {"Idempotency-Key": str(key)},
    }[access]
    response = cancel(order_client, first, headers=headers)
    assert response.status_code == 404
    absent = cancel(order_client, {"order_id": str(uuid4())}, headers=headers)
    assert absent.status_code == 404 and absent.json() == response.json()
    with menu_db.sessions() as db:
        assert list(db.scalars(select(Order.status))) == ["Solicitud recibida", "Solicitud recibida"]
    assert first["tracking"]["token"] not in response.text
    assert counts(menu_db.sessions) == (2, 4, 2)


@pytest.mark.parametrize("status", ["Aceptado", "En preparación", "Rechazado", "Cancelado"])
def test_authorized_cancellation_rejects_every_nonpending_state(order_client, menu_db, detail_catalog, status):
    receipt = submit(order_client, payload(detail_catalog)).json()
    with menu_db.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = status
    response = cancel(order_client, receipt, headers=authorization(receipt["tracking"]["token"]))
    assert response.status_code == 409
    with menu_db.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == status
    # Un acceso incorrecto no debe revelar siquiera el estado del pedido.
    assert cancel(order_client, receipt).status_code == 404


def registered_request(backend, product):
    signed_in = login(backend, "customer")
    body = {"cart": {"items": [item(product)]}, "data": {"modality": "dine_in", "name": "Alias"}}
    created = submit(backend.client, body, headers=csrf_headers(signed_in))
    assert created.status_code == 201, created.text
    return signed_in, created.json()


def test_customer_cancels_own_order_only_with_csrf_and_trusted_origin(registered_order_backend):
    b, product = registered_order_backend
    signed_in, receipt = registered_request(b, product)
    for headers in (
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "X-CSRF-Token": "0" * 64},
        {**csrf_headers(signed_in), "Origin": "https://evil.invalid"},
    ):
        assert cancel(b.client, receipt, headers=headers).status_code == 403
        with b.sessions() as db:
            assert db.get(Order, UUID(receipt["order_id"])).status == "Solicitud recibida"
    accepted = cancel(b.client, receipt, headers=csrf_headers(signed_in))
    assert accepted.status_code == 200 and accepted.json() == {"order_id": receipt["order_id"], "status": "Cancelado"}
    assert cancel(b.client, receipt, headers=csrf_headers(signed_in)).status_code == 409
    with b.sessions() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        assert order.account_id == b.ids["customer"][0] and order.status == "Cancelado"
    assert counts(b.sessions) == (1, 1, 0)


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_other_accounts_have_no_cancellation_override(registered_order_backend, role):
    b, product = registered_order_backend
    _, receipt = registered_request(b, product)
    other_user = login(b, role)
    denied = cancel(b.client, receipt, headers=csrf_headers(other_user))
    assert denied.status_code == 404 and receipt["order_id"] not in denied.text
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == "Solicitud recibida"
    b.client.cookies.clear()
    assert cancel(b.client, receipt).status_code == 404


def test_invalid_session_does_not_fall_back_to_guest_token(registered_order_backend):
    b, product = registered_order_backend
    guest = submit(b.client, {"cart": {"items": [item(product)]}, "data": {"modality": "dine_in", "name": "Alias"}}).json()
    signed_in = login(b, "customer")
    b.client.cookies.set(cookie_name(b.config), "invalid", domain="localhost.local", path="/")
    headers = {**csrf_headers(signed_in), **authorization(guest["tracking"]["token"])}
    assert cancel(b.client, guest, headers=headers).status_code == 401
    with b.sessions() as db:
        assert db.get(Order, UUID(guest["order_id"])).status == "Solicitud recibida"
    b.client.cookies.clear()
    assert cancel(b.client, guest, headers=authorization(guest["tracking"]["token"])).status_code == 200


def test_error_after_update_rolls_back_cancellation(order_client, menu_db, detail_catalog, monkeypatch, caplog):
    receipt = submit(order_client, payload(detail_catalog)).json()
    original = order_cancellation_service.cancel_order
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OperationalError("synthetic-private-sql", {}, Exception("synthetic-private-error"))
    with monkeypatch.context() as patch:
        patch.setattr(order_cancellation_service, "cancel_order", fail)
        response = cancel(order_client, receipt, headers=authorization(receipt["tracking"]["token"]))
    assert response.status_code == 503 and "cancelación" in response.json()["detail"]
    assert "synthetic-private" not in response.text + caplog.text
    assert receipt["tracking"]["token"] not in response.text + caplog.text
    with menu_db.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == "Solicitud recibida"
    assert counts(menu_db.sessions) == (1, 2, 1)
    assert cancel(order_client, receipt, headers=authorization(receipt["tracking"]["token"])).status_code == 200


def create_committed_order(sessions, product):
    body = OrderSubmissionInput(cart={"items": [item(product)]}, data={"modality": "dine_in", "name": "Alias"})
    with sessions() as db, db.begin():
        return order_submission_service.submit_order(db, body, uuid4())


def test_simultaneous_cancellations_have_only_one_winner(committed_orders):
    sessions, product = committed_orders
    receipt = create_committed_order(sessions, product)
    barrier = Barrier(2)
    def send():
        try:
            with sessions() as db, db.begin():
                barrier.wait(timeout=5)
                order_cancellation_service.cancel_order(db, receipt.order_id, guest_token=receipt.tracking.token)
            return "cancelled"
        except order_cancellation_service.OrderNotPendingError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(results) == ["cancelled", "conflict"]
    with sessions() as db:
        assert db.get(Order, receipt.order_id).status == "Cancelado"
    assert counts(sessions) == (1, 1, 0)


@pytest.mark.parametrize("winner", ["Aceptado", "Cancelado"])
def test_contended_state_updates_recheck_pending_state_after_waiting(committed_orders, winner):
    sessions, product = committed_orders
    receipt = create_committed_order(sessions, product)
    reached_update = Event()
    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith('UPDATE "Orders"'):
            reached_update.set()
    # Simula exclusivamente en la prueba la transición futura de aceptación.
    accept = update(Order).where(Order.order_id == receipt.order_id, Order.status == "Solicitud recibida").values(
        status="Aceptado").returning(Order.order_id)
    def contender():
        try:
            with sessions() as db, db.begin():
                if winner == "Aceptado":
                    order_cancellation_service.cancel_order(db, receipt.order_id, guest_token=receipt.tracking.token)
                    return "cancelled"
                return db.scalar(accept)
        except order_cancellation_service.OrderNotPendingError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=1) as executor:
        with sessions() as db, db.begin():
            if winner == "Aceptado":
                assert db.scalar(accept) == receipt.order_id
            else:
                order_cancellation_service.cancel_order(db, receipt.order_id, guest_token=receipt.tracking.token)
            engine = sessions.kw["bind"]
            event.listen(engine, "before_cursor_execute", observe)
            try:
                future = executor.submit(contender)
                assert reached_update.wait(timeout=5)
            finally:
                event.remove(engine, "before_cursor_execute", observe)
        result = future.result(timeout=10)
    assert result == ("conflict" if winner == "Aceptado" else None)
    with sessions() as db:
        assert db.get(Order, receipt.order_id).status == winner


@pytest.mark.parametrize("auth_enabled", [False, True])
def test_cancellation_cors_is_limited_to_its_route(auth_enabled):
    origin = "http://localhost:5173"
    config = auth_config() if auth_enabled else Settings(_env_file=None, app_env="test", admin_enabled=False,
        database_url="postgresql://unused@localhost/unused", cors_origins=[origin])
    cancel_path = f"/api/orders/{uuid4()}/cancel"
    with TestClient(create_app(config)) as client:
        response = client.options(cancel_path, headers={"Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,Authorization,X-CSRF-Token"})
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == origin
        assert (response.headers.get("access-control-allow-credentials") == "true") is auth_enabled
        assert client.options(cancel_path, headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"}).status_code == 400
        assert client.options("/api/orders", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization"}).status_code == 400


def test_cancellation_accepts_only_empty_json_and_trusted_origins(order_client, menu_db, detail_catalog):
    receipt = submit(order_client, payload(detail_catalog)).json()
    headers = authorization(receipt["tracking"]["token"])
    assert order_client.post(path(receipt), json={"status": "Aceptado", "account_id": 1}, headers=headers).status_code == 422
    assert order_client.post(path(receipt), json={}, headers={**headers, "Content-Type": "text/plain"}).status_code == 415
    assert cancel(order_client, receipt, headers={**headers, "Origin": "https://evil.invalid"}).status_code == 403
    with menu_db.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == "Solicitud recibida"

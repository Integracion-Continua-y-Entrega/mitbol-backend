"""HU-021 en PostgreSQL desechable: solicitudes, transacciones e idempotencia."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.auth_http import cookie_name
from app.core.config import Settings
from app.main import create_app
from app.models import (
    Order, OrderDetail, OrderDetailComplement, Product, ProductComplement, ProductVariant, RestaurantOrderSettings,
)
from app.schemas.orders import OrderSubmissionInput
from app.services import order_submission_service, order_summary_service
from test_auth_api import backend as auth_backend, csrf_headers, login, password_hash, ORIGIN
from test_auth_persistence import auth_database, config_for
from test_cart import item
from test_menu import add_product, detail_catalog


PATH = "/api/orders"
REVISION = "20261119_0009"
PREVIOUS = "20261115_0008"


@pytest.fixture
def menu_db(current_catalog_database):
    # Conserva el grafo de catálogo existente y revierte incluso el DDL nuevo.
    with current_catalog_database.connect() as connection, connection.begin() as transaction:
        command.upgrade(config_for(connection), REVISION)
        connection.execute(update(RestaurantOrderSettings).values(accepting_orders=True))
        sessions = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False,
                                join_transaction_mode="create_savepoint")
        yield SimpleNamespace(connection=connection, sessions=sessions)
        transaction.rollback()


@pytest.fixture
def order_client(menu_db):
    app = create_app(Settings(_env_file=None, app_env="test", admin_enabled=False,
                              database_url="postgresql://unused@localhost/unused"))
    original = app.state.session_factory
    with TestClient(app) as client:
        app.state.session_factory = menu_db.sessions
        try:
            yield client
        finally:
            app.state.session_factory = original


def payload(catalog, *, data=None):
    return {"cart": {"items": [
        item(catalog.product, quantity=2, complements=[catalog.shared.complements[1].product_complement_id]),
        item(catalog.other, quantity=3),
    ]}, "data": data or {"modality": "dine_in", "name": " Invitado "}}


def submit(client, body, *, key=None, headers=None):
    return client.post(PATH, json=body, headers={"Idempotency-Key": str(key or uuid4()), **(headers or {})})


def counts(sessions):
    with sessions() as db:
        return tuple(db.scalar(select(func.count()).select_from(model))
                     for model in (Order, OrderDetail, OrderDetailComplement))


@pytest.mark.parametrize("data", [
    {"modality": "dine_in", "name": "  Invitado  "},
    {"modality": "pickup", "name": "  Invitado  ", "phone": "+52 (228) 123-4567"},
])
def test_guest_order_persists_complete_historical_request(order_client, menu_db, detail_catalog, data):
    c = detail_catalog
    body = payload(c, data=data)
    response = submit(order_client, body)
    assert response.status_code == 201, response.text
    result = response.json()
    assert UUID(result["order_id"]).version == 4
    assert result["status"] == "Solicitud recibida" and result["created_at"]
    assert result["subtotal"] == result["total"] == "344.00"
    assert result["discount_total"] == "0.00" and result["discounts_status"] == "not_integrated"
    assert result["data"]["name"] == "Invitado"
    assert result["data"].get("phone") == ("+522281234567" if data["modality"] == "pickup" else None)
    assert "cookie" not in response.request.headers and response.headers["cache-control"] == "no-store"
    assert "account_id" not in result and "idempotency_key" not in result and "request_hash" not in result
    assert [row["subtotal"] for row in result["items"]] == ["209.00", "135.00"]
    with menu_db.sessions() as db:
        order = db.get(Order, UUID(result["order_id"]))
        assert order.account_id is None and order.status == "Solicitud recibida"
        assert order.created_at.tzinfo is not None
        assert [int(row.quantity) for row in order.details] == [2, 3]
        first = order.details[0]
        assert first.product_name == c.product.name and first.variant_name == c.product.variants[0].name
        assert first.base_price == Decimal("89.00") and first.unit_total == Decimal("104.50")
        assert first.complements[0].name_snapshot == c.shared.complements[1].name
        assert first.complements[0].unit_price == Decimal("15.50")
    assert counts(menu_db.sessions) == (1, 2, 1)


def test_same_key_returns_original_after_price_name_and_acceptance_changes(order_client, menu_db, detail_catalog):
    c = detail_catalog
    key, body = uuid4(), payload(c)
    first = submit(order_client, body, key=key)
    assert first.status_code == 201
    with menu_db.sessions.begin() as db:
        db.get(Product, c.product.product_id).name = "Nombre posterior"
        variant = db.get(ProductVariant, c.product.variants[0].product_variant_id)
        variant.name, variant.price, variant.is_available = "Variante posterior", Decimal("999.00"), False
        option = db.get(ProductComplement, c.shared.complements[1].product_complement_id)
        option.name, option.price, option.is_active = "Opción posterior", Decimal("99.00"), False
        db.get(RestaurantOrderSettings, 1).accepting_orders = False
    repeated = submit(order_client, body, key=key)
    assert repeated.status_code == 201 and repeated.json() == first.json()
    assert counts(menu_db.sessions) == (1, 2, 1)
    assert submit(order_client, body).status_code == 409


def test_same_key_with_changed_content_conflicts_without_leaking_confirmation(order_client, menu_db, detail_catalog):
    key, body = uuid4(), payload(detail_catalog)
    first = submit(order_client, body, key=key)
    assert first.status_code == 201
    body["cart"]["items"][0]["quantity"] = 3
    response = submit(order_client, body, key=key)
    assert response.status_code == 409
    assert first.json()["order_id"] not in response.text and "Invitado" not in response.text
    assert counts(menu_db.sessions) == (1, 2, 1)


@pytest.mark.parametrize("condition", ["closed", "missing_settings"])
def test_restaurant_must_accept_orders(order_client, menu_db, detail_catalog, condition):
    with menu_db.sessions.begin() as db:
        if condition == "closed":
            db.get(RestaurantOrderSettings, 1).accepting_orders = False
        else:
            db.execute(delete(RestaurantOrderSettings))
    response = submit(order_client, payload(detail_catalog))
    assert response.status_code == 409 and "aceptando pedidos" in response.json()["detail"]
    assert counts(menu_db.sessions) == (0, 0, 0)


@pytest.mark.parametrize("change", [
    "empty", "bad_quantity", "foreign_variant", "foreign_complement", "unavailable_variant", "unavailable_product",
    "inactive_complement", "missing_product", "missing_phone", "empty_name", "unsupported_modality", "extra_phone",
    "client_total", "client_price", "client_account",
])
def test_invalid_requests_never_persist_partial_orders(order_client, menu_db, detail_catalog, change):
    c, body = detail_catalog, payload(detail_catalog)
    row = body["cart"]["items"][1]
    expected = 422
    if change == "empty":
        body["cart"]["items"] = []
    elif change == "bad_quantity":
        row["quantity"] = 0
    elif change == "foreign_variant":
        row["product_variant_id"] = c.product.variants[0].product_variant_id
    elif change == "foreign_complement":
        row["complement_ids"] = [c.exclusive.complements[0].product_complement_id]
    elif change == "missing_product":
        row["product_id"], expected = 2147483647, 404
    elif change in {"unavailable_variant", "unavailable_product", "inactive_complement"}:
        with menu_db.sessions.begin() as db:
            if change == "unavailable_variant":
                db.get(ProductVariant, row["product_variant_id"]).is_available = False
            elif change == "unavailable_product":
                db.get(Product, row["product_id"]).is_available = False
            else:
                db.get(ProductComplement, c.shared.complements[1].product_complement_id).is_active = False
        expected = 409
    elif change == "missing_phone":
        body["data"] = {"modality": "pickup", "name": "Invitado"}
    elif change == "empty_name":
        body["data"]["name"] = "  "
    elif change == "unsupported_modality":
        body["data"]["modality"] = "delivery"
    elif change == "extra_phone":
        body["data"]["phone"] = "2281234567"
    elif change == "client_total":
        body["total"] = "0.01"
    elif change == "client_price":
        row["unit_price"] = "0.01"
    else:
        body["account_id"] = 1
    response = submit(order_client, body)
    assert response.status_code == expected, response.text
    assert counts(menu_db.sessions) == (0, 0, 0)


@pytest.mark.parametrize("key", [None, "not-a-uuid", str(UUID(int=1)), str(uuid4()) + " "])
def test_idempotency_key_is_required_uuid4(order_client, menu_db, detail_catalog, key):
    response = order_client.post(PATH, json=payload(detail_catalog), headers={} if key is None else {"Idempotency-Key": key})
    assert response.status_code == 422
    assert counts(menu_db.sessions) == (0, 0, 0)


def test_submission_recalculates_prices_via_hu019(order_client, menu_db, detail_catalog, monkeypatch):
    c, body = detail_catalog, payload(detail_catalog)
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[0].product_variant_id).price = Decimal("0.10")
        db.get(ProductComplement, c.shared.complements[1].product_complement_id).price = Decimal("0.20")
    original, calls = order_summary_service.summarize_cart, []
    def summarize(db, cart):
        calls.append(cart)
        return original(db, cart)
    monkeypatch.setattr(order_summary_service, "summarize_cart", summarize)
    response = submit(order_client, body)
    assert response.status_code == 201, response.text
    assert response.json()["items"][0]["subtotal"] == "0.60"
    assert response.json()["total"] == "135.60" and len(calls) == 1
    with menu_db.sessions() as db:
        assert db.scalar(select(Order.total)) == Decimal("135.60")


def test_exact_prices_large_quantities_and_repeated_configurations_survive_storage(order_client, menu_db, detail_catalog):
    c = detail_catalog
    option_id = c.shared.complements[1].product_complement_id
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[0].product_variant_id).price = Decimal("0.0000000000000000001")
        db.get(ProductComplement, option_id).price = Decimal("0.0000000000000000002")
    quantity = 123456789012345678901234567890
    body = {"cart": {"items": [item(c.product, quantity=quantity, complements=[option_id]),
                               item(c.product, quantity=2, complements=[option_id])]},
            "data": {"modality": "dine_in", "name": "Alias"}}
    key = uuid4()
    response = submit(order_client, body, key=key)
    assert response.status_code == 201, response.text
    assert response.json()["total"] == "37037036703.7037036703703703676"
    assert [row["quantity"] for row in response.json()["items"]] == [quantity, 2]
    with menu_db.sessions() as db:
        assert db.scalar(select(Order.total)) == Decimal("37037036703.7037036703703703676")
        assert list(db.scalars(select(OrderDetail.quantity).order_by(OrderDetail.line_index))) == [quantity, 2]
    assert submit(order_client, body, key=key).json() == response.json()


def test_failed_detail_insert_rolls_back_header_lines_and_key_then_retry_succeeds(order_client, menu_db, detail_catalog):
    key, body = uuid4(), payload(detail_catalog)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith('INSERT INTO "OrderDetailComplements"'):
            raise OperationalError("synthetic-private-sql", {}, Exception("synthetic-private-error"))
    event.listen(menu_db.connection, "before_cursor_execute", fail)
    try:
        response = submit(order_client, body, key=key)
    finally:
        event.remove(menu_db.connection, "before_cursor_execute", fail)
    assert response.status_code == 503
    assert "synthetic-private" not in response.text
    assert counts(menu_db.sessions) == (0, 0, 0)
    assert submit(order_client, body, key=key).status_code == 201
    assert counts(menu_db.sessions) == (1, 2, 1)


def _seed_committed(engine):
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with engine.begin() as db:
        command.upgrade(config_for(db), REVISION)
    with sessions.begin() as db:
        db.get(RestaurantOrderSettings, 1).accepting_orders = True
        product = Product(loyverse_id=uuid4().hex, name="Solicitud sintética", is_active=True, is_available=True,
                          synced_at=datetime.now(timezone.utc))
        product.variants = [ProductVariant(loyverse_id=uuid4().hex, name="Única", price=Decimal("0.10"),
            is_available=True, is_commercially_available=True, synced_at=datetime.now(timezone.utc))]
        db.add(product)
    return sessions, product


def _cleanup_committed(engine, sessions, product):
    with sessions.begin() as db:
        db.execute(delete(Order))
        db.execute(delete(ProductVariant).where(ProductVariant.product_id == product.product_id))
        db.execute(delete(Product).where(Product.product_id == product.product_id))
    with engine.begin() as db:
        command.downgrade(config_for(db), PREVIOUS)


@pytest.fixture
def committed_orders(auth_database):
    sessions, product = _seed_committed(auth_database)
    try:
        yield sessions, product
    finally:
        _cleanup_committed(auth_database, sessions, product)


def test_concurrent_retries_commit_exactly_one_order(committed_orders):
    sessions, product = committed_orders
    body = OrderSubmissionInput(cart={"items": [item(product, quantity=3)]}, data={"modality": "dine_in", "name": "Alias"})
    barrier, key = Barrier(2), uuid4()
    def send():
        with sessions() as db, db.begin():
            barrier.wait(timeout=5)
            return order_submission_service.submit_order(db, body, key).model_dump(mode="json")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1] and results[0]["total"] == "0.30"
    assert counts(sessions) == (1, 1, 0)


@pytest.fixture
def registered_order_backend(auth_backend):
    b = auth_backend
    engine = b.sessions.kw["bind"]
    sessions, product = _seed_committed(engine)
    try:
        yield b, product
    finally:
        _cleanup_committed(engine, sessions, product)


@pytest.mark.parametrize("role", ["customer", "admin", "staff"])
def test_registered_requests_use_authenticated_account_and_csrf(registered_order_backend, role):
    b, product = registered_order_backend
    signed_in = login(b, role)
    assert signed_in.status_code == 200
    body = {"cart": {"items": [item(product)]}, "data": {"modality": "dine_in", "name": "Alias para este pedido"}}
    key = uuid4()
    denied = submit(b.client, body, key=key, headers={"Origin": ORIGIN})
    assert denied.status_code == 403 and counts(b.sessions) == (0, 0, 0)
    accepted = submit(b.client, body, key=key, headers=csrf_headers(signed_in))
    assert accepted.status_code == 201, accepted.text
    with b.sessions() as db:
        order = db.get(Order, UUID(accepted.json()["order_id"]))
        assert order.account_id == b.ids[role][0] and order.requester_name == "Alias para este pedido"
    repeated = submit(b.client, body, key=key, headers=csrf_headers(signed_in))
    assert repeated.json() == accepted.json() and counts(b.sessions) == (1, 1, 0)
    assert b.client.get("/api/auth/me", headers={"Origin": ORIGIN}).json()["user"] == signed_in.json()["user"]


@pytest.mark.parametrize("condition", ["invalid", "expired", "revoked"])
def test_invalid_session_is_not_silently_downgraded_to_guest(registered_order_backend, condition):
    b, product = registered_order_backend
    signed_in = login(b, "customer")
    if condition == "invalid":
        b.client.cookies.set(cookie_name(b.config), "invalid", domain="localhost.local", path="/")
    elif condition == "expired":
        b.now.value += timedelta(hours=9)
    else:
        assert b.client.post("/api/auth/logout", json={}, headers=csrf_headers(signed_in)).status_code == 204
        # Reponer el token revocado para comprobar el rechazo del servidor.
        b.client.cookies.set(cookie_name(b.config), signed_in.cookies.get(cookie_name(b.config)), domain="localhost.local", path="/")
    response = submit(b.client, {"cart": {"items": [item(product)]}, "data": {"modality": "dine_in", "name": "Alias"}},
                      headers=csrf_headers(signed_in))
    assert response.status_code == 401 and counts(b.sessions) == (0, 0, 0)


def test_idempotency_key_cannot_switch_account_ownership(registered_order_backend):
    b, product = registered_order_backend
    body = {"cart": {"items": [item(product)]}, "data": {"modality": "dine_in", "name": "Alias"}}
    key = uuid4()
    guest = submit(b.client, body, key=key)
    assert guest.status_code == 201
    signed_in = login(b, "customer")
    denied = submit(b.client, body, key=key, headers=csrf_headers(signed_in))
    assert denied.status_code == 409 and guest.json()["order_id"] not in denied.text
    assert counts(b.sessions) == (1, 1, 0)


@pytest.mark.parametrize("authenticated", [False, True])
def test_orders_cors_allows_submission_headers_without_exposing_other_routes(authenticated):
    from test_auth_phase2_core import config as auth_config
    public_origin, auth_origin = "http://localhost:5173", "http://localhost:3000"
    config = (auth_config(auth_origins=[auth_origin], cors_origins=[public_origin]) if authenticated else
              Settings(_env_file=None, app_env="test", admin_enabled=False, cors_origins=[public_origin],
                       database_url="postgresql://unused@localhost/unused"))
    with TestClient(create_app(config)) as client:
        for origin in [public_origin, *([auth_origin] if authenticated else [])]:
            response = client.options(PATH, headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,Idempotency-Key,X-CSRF-Token"})
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            assert (response.headers.get("access-control-allow-credentials") == "true") is authenticated
        assert client.options(PATH, headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"}).status_code == 400
        assert client.options("/api/cart/summary", headers={"Origin": public_origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Idempotency-Key"}).status_code == 400


def test_guest_rejects_untrusted_origins_and_form_submission(order_client, menu_db, detail_catalog):
    body = payload(detail_catalog)
    assert submit(order_client, body, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert order_client.post(PATH, json=body, headers={"Content-Type": "text/plain", "Idempotency-Key": str(uuid4())}).status_code == 415
    assert counts(menu_db.sessions) == (0, 0, 0)

"""HU-022: acceso privado, lectura histórica y compatibilidad con HU-021."""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from app.core.session_tokens import generate_session_token, hash_session_token
from app.main import create_app
from app.models import Order, Product, ProductComplement, ProductVariant, RestaurantOrderSettings
from app.services import order_tracking_service
from test_auth_api import csrf_headers, login
from test_auth_phase2_core import config as auth_config
from test_cloud_configuration import cloud_settings
from test_menu import add_product, capture_sql, detail_catalog
from test_order_submission import (
    auth_backend, auth_database, counts, menu_db, order_client, password_hash, payload,
    registered_order_backend, submit,
)
from test_cart import item


def authorization(token):
    return {"Authorization": f"Bearer {token}"}


def test_guest_receives_private_access_and_tracking_excludes_personal_data(order_client, menu_db, detail_catalog):
    body = payload(detail_catalog, data={"modality": "pickup", "name": "Nombre privado de HU022", "phone": "+522281234567"})
    created = submit(order_client, body)
    assert created.status_code == 201, created.text
    receipt = created.json()
    access = receipt["tracking"]
    assert hash_session_token(access["token"])
    assert access["token_type"] == "Bearer"
    assert access["path"] == f'/api/orders/{receipt["order_id"]}/tracking'
    assert access["token"] not in access["path"]
    with capture_sql(menu_db.connection) as statements:
        response = order_client.get(access["path"], headers=authorization(access["token"]))
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"order_id", "status", "created_at", "modality", "currency", "items", "subtotal", "discount_total", "total", "discounts_status", "applied_discounts", "pending_discounts", "coupon"}
    assert result["order_id"] == receipt["order_id"] and result["status"] == "Solicitud recibida"
    assert result["created_at"] == receipt["created_at"] and result["modality"] == "pickup"
    assert result["items"] == receipt["items"] and result["total"] == receipt["total"] == "344.00"
    assert "Nombre privado de HU022" not in response.text and "+522281234567" not in response.text
    assert access["token"] not in response.text
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer"
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "COMMIT")) for sql in statements)
    assert not any("requester_name" in sql or "requester_phone" in sql for sql in statements)


@pytest.mark.parametrize("status", ["Aceptado", "En preparación", "Rechazado", "Cancelado"])
def test_tracking_reads_current_status_and_original_prices_even_when_catalog_changes(
    order_client, menu_db, detail_catalog, status,
):
    c, body, key = detail_catalog, payload(detail_catalog), uuid4()
    receipt = submit(order_client, body, key=key).json()
    access = receipt["tracking"]
    with menu_db.sessions.begin() as db:
        db.get(Order, UUID(receipt["order_id"])).status = status
        db.get(Product, c.product.product_id).name = "Nombre posterior"
        db.get(ProductVariant, c.product.variants[0].product_variant_id).is_available = False
        db.get(ProductComplement, c.shared.complements[1].product_complement_id).is_active = False
        db.get(RestaurantOrderSettings, 1).accepting_orders = False
    response = order_client.get(access["path"], headers=authorization(access["token"]))
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert response.json()["items"] == receipt["items"] and response.json()["total"] == receipt["total"]
    replay = submit(order_client, body, key=key)
    assert replay.status_code == 201 and replay.json()["tracking"] == access
    assert replay.json()["status"] == status
    assert counts(menu_db.sessions) == (1, 2, 1)


@pytest.mark.parametrize("invalid", [None, "", "Basic anything", "Bearer invalid", "Bearer " + "a" * 42,
                                      "Bearer " + "á" * 43, "Bearer " + "_" * 43, "Bearer wrong extra"])
def test_invalid_access_does_not_reveal_order_existence_or_credentials(order_client, menu_db, detail_catalog, invalid, caplog):
    receipt = submit(order_client, payload(detail_catalog)).json()
    access = receipt["tracking"]
    headers = {} if invalid is None else {"Authorization": invalid}
    # HTTPX sólo admite ASCII en cabeceras; bytes permiten probar el rechazo seguro de UTF-8.
    if invalid is not None and "á" in invalid:
        headers = {b"Authorization": invalid.encode("utf-8")}
    existing = order_client.get(access["path"], headers=headers)
    absent = order_client.get(f"/api/orders/{uuid4()}/tracking", headers=headers)
    assert existing.status_code == absent.status_code == 404
    assert existing.json() == absent.json() == {"detail": "Pedido o acceso privado no válidos."}
    assert existing.headers["cache-control"] == "no-store"
    assert access["token"] not in existing.text + absent.text + caplog.text


def test_token_cannot_access_another_order_and_identifiers_alone_are_insufficient(order_client, menu_db, detail_catalog):
    first = submit(order_client, payload(detail_catalog)).json()
    second = submit(order_client, payload(detail_catalog)).json()
    assert first["tracking"]["token"] != second["tracking"]["token"]
    path = first["tracking"]["path"]
    attempts = [
        order_client.get(path),
        order_client.get(path, headers=authorization(second["tracking"]["token"])),
        order_client.get(path, headers=authorization(first["order_id"])),
        order_client.get(path, params={"token": first["tracking"]["token"]}),
        order_client.get(f"/api/orders/{uuid4()}/tracking", headers=authorization(first["tracking"]["token"])),
    ]
    assert all(response.status_code == 404 for response in attempts)
    assert all(response.json() == attempts[0].json() for response in attempts)
    assert counts(menu_db.sessions) == (2, 4, 2)


def test_idempotent_retry_redelivers_same_private_access_and_legacy_order_gets_one(order_client, menu_db, detail_catalog):
    key, body = uuid4(), payload(detail_catalog)
    first = submit(order_client, body, key=key).json()
    assert submit(order_client, body, key=key).json() == first
    with menu_db.sessions.begin() as db:
        db.get(Order, UUID(first["order_id"])).guest_tracking_token = None
    assert order_client.get(first["tracking"]["path"], headers=authorization(first["tracking"]["token"])).status_code == 404
    recovered = submit(order_client, body, key=key).json()
    assert recovered["order_id"] == first["order_id"] and recovered["tracking"]["token"] != first["tracking"]["token"]
    assert submit(order_client, body, key=key).json() == recovered
    assert order_client.get(recovered["tracking"]["path"], headers=authorization(recovered["tracking"]["token"])).status_code == 200
    assert counts(menu_db.sessions) == (1, 2, 1)


def test_authorization_helper_is_reusable_without_http(menu_db, order_client, detail_catalog):
    receipt = submit(order_client, payload(detail_catalog)).json()
    with menu_db.sessions() as db:
        order = order_tracking_service.authorize_guest_order(db, UUID(receipt["order_id"]), receipt["tracking"]["token"])
        assert order.order_id == UUID(receipt["order_id"])
        assert not db.new and not db.dirty and not db.deleted
        with pytest.raises(order_tracking_service.GuestOrderAccessError):
            order_tracking_service.authorize_guest_order(db, order.order_id, generate_session_token().value)


def test_registered_order_does_not_gain_guest_access(registered_order_backend):
    b, product = registered_order_backend
    signed_in = login(b, "customer")
    result = submit(b.client, {"cart": {"items": [item(product)]}, "data": {"modality": "dine_in", "name": "Alias"}},
                    headers=csrf_headers(signed_in))
    assert result.status_code == 201
    receipt = result.json()
    assert receipt["tracking"] is None
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).guest_tracking_token is None
    response = b.client.get(f'/api/orders/{receipt["order_id"]}/tracking', headers=authorization(generate_session_token().value))
    assert response.status_code == 404


@pytest.mark.parametrize("auth_enabled", [False, True])
def test_tracking_cors_allows_only_get_with_private_header(auth_enabled):
    origin = "http://localhost:5173"
    config = auth_config() if auth_enabled else cloud_settings()
    path = f"/api/orders/{uuid4()}/tracking"
    with TestClient(create_app(config)) as client:
        preflight = client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                                 "Access-Control-Request-Headers": "Authorization"})
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in preflight.headers
        assert client.options(path, headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "GET"}).status_code == 400
        assert client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "POST"}).status_code == 400
        assert client.options("/api/cart/summary", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization"}).status_code == 400


def test_tracking_database_errors_are_sanitized(order_client, menu_db, detail_catalog, monkeypatch, caplog):
    from sqlalchemy.exc import OperationalError
    receipt = submit(order_client, payload(detail_catalog)).json()
    def fail(*args):
        raise OperationalError("synthetic-private-sql", {}, Exception("synthetic-private-error"))
    monkeypatch.setattr(order_tracking_service, "authorize_guest_order", fail)
    response = order_client.get(receipt["tracking"]["path"], headers=authorization(receipt["tracking"]["token"]))
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "synthetic-private" not in response.text + caplog.text
    assert receipt["tracking"]["token"] not in response.text + caplog.text

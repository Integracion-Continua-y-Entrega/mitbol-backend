"""Validación de carritos con el mismo PostgreSQL aislado que HU-017."""

from decimal import Decimal
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings
from app.db.database import get_db
from app.main import create_app
from app.models import Product, ProductVariant
from app.schemas.cart import CartValidationInput
from app.services import cart_service, menu_service, product_options_service
from test_auth_phase2_core import config as auth_config
from test_cloud_configuration import cloud_settings
from test_menu import add_product, api_app, capture_sql, detail_catalog, menu_client, menu_db


PATH = "/api/cart/validate"


def item(product, *, quantity=1, variant_index=0, complements=()):
    return {"product_id": product.product_id,
            "product_variant_id": product.variants[variant_index].product_variant_id,
            "quantity": quantity, "complement_ids": list(complements)}


def test_cart_validates_multiple_products_and_configurations_without_writes(menu_client, menu_db, detail_catalog):
    c = detail_catalog
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[1].product_variant_id).is_available = True
    payload = {"items": [
        item(c.product, quantity=2, complements=[c.shared.complements[1].product_complement_id]),
        item(c.other, complements=[c.foreign.complements[0].product_complement_id]),
        item(c.product, quantity=3, variant_index=1),
        item(c.product, quantity=4),
    ]}
    with capture_sql(menu_db.connection) as statements:
        response = menu_client.post(PATH, json=payload)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "authorization" not in response.request.headers and "cookie" not in response.request.headers
    body = response.json()
    assert set(body) == {"valid", "items"} and body["valid"] is True
    assert [row["quantity"] for row in body["items"]] == [2, 1, 3, 4]
    assert [row["selection"]["product_id"] for row in body["items"]] == [
        c.product.product_id, c.other.product_id, c.product.product_id, c.product.product_id]
    assert [row["selection"]["unit_total"] for row in body["items"]] == ["104.50", "53.00", "129.00", "89.00"]
    assert not any(sql.lstrip().upper().startswith(
        ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE", "COMMIT")) for sql in statements)


def test_empty_cart_is_valid_without_queries(api_app):
    db = Mock()
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        assert client.post(PATH, json={"items": []}).json() == {"valid": True, "items": []}
    assert db.mock_calls == []


@pytest.mark.parametrize("field,value", [
    ("quantity", 0), ("quantity", -1), ("quantity", 1.5), ("quantity", 1.0),
    ("quantity", "2"), ("quantity", True), ("quantity", None),
    ("product_id", 0), ("product_id", True), ("product_id", "1"),
    ("product_variant_id", -1), ("product_variant_id", 1.0),
    ("complement_ids", [0]), ("complement_ids", [True]), ("complement_ids", [2, 2]),
    ("complement_ids", [{"product_complement_id": 2, "price": "0.01"}]),
    ("price", "0.01"), ("unit_total", "0.01"), ("unit_price", "0.01"),
])
def test_invalid_cart_lines_are_rejected_before_query(api_app, field, value):
    db = Mock()
    api_app.dependency_overrides[get_db] = lambda: db
    row = {"product_id": 1, "product_variant_id": 1, "quantity": 1}
    row[field] = value
    with TestClient(api_app) as client:
        response = client.post(PATH, json={"items": [row]})
    assert response.status_code == 422
    assert db.mock_calls == []


@pytest.mark.parametrize("payload", [
    {}, {"items": None}, {"items": "invalid"}, {"items": [None]},
    {"items": [{"product_id": 1, "product_variant_id": 1}]},
    {"items": [], "total": "0.01"},
])
def test_invalid_cart_structure_is_rejected_before_query(api_app, payload):
    db = Mock()
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        assert client.post(PATH, json=payload).status_code == 422
    assert db.mock_calls == []


@pytest.mark.parametrize("condition,status", [
    ("missing_product", 404), ("missing_variant", 422), ("foreign_variant", 422),
    ("missing_complement", 422), ("foreign_complement", 422), ("inactive_complement", 409),
    ("inactive_product", 404), ("unavailable_product", 409),
    ("unavailable_variant", 409), ("commercially_unavailable_variant", 409),
])
def test_invalid_later_line_rejects_whole_cart_and_identifies_its_index(
    menu_client, menu_db, detail_catalog, condition, status,
):
    c = detail_catalog
    invalid = item(c.product)
    if condition == "missing_product":
        invalid["product_id"] = 2147483647
    elif condition == "missing_variant":
        invalid["product_variant_id"] = 2147483647
    elif condition == "foreign_variant":
        invalid["product_variant_id"] = c.other.variants[0].product_variant_id
    elif condition in {"missing_complement", "foreign_complement", "inactive_complement"}:
        option_id = {"missing_complement": 2147483647,
                     "foreign_complement": c.foreign.complements[0].product_complement_id,
                     "inactive_complement": c.shared.complements[2].product_complement_id}[condition]
        invalid["complement_ids"] = [option_id]
    else:
        with menu_db.sessions.begin() as db:
            if condition in {"inactive_product", "unavailable_product"}:
                field = "is_active" if condition == "inactive_product" else "is_available"
                setattr(db.get(Product, c.product.product_id), field, False)
            else:
                field = "is_available" if condition == "unavailable_variant" else "is_commercially_available"
                setattr(db.get(ProductVariant, invalid["product_variant_id"]), field, False)
    response = menu_client.post(PATH, json={"items": [item(c.other), invalid]})
    assert response.status_code == status
    assert set(response.json()) == {"detail"}
    assert response.json()["detail"]["item_index"] == 1
    assert response.json()["detail"]["message"]


def test_cart_service_reuses_hu017_and_preserves_decimal_prices(menu_db, detail_catalog, monkeypatch):
    c = detail_catalog
    payload = CartValidationInput(items=[item(c.product, quantity=2), item(c.other)])
    original = product_options_service.validate_product_selection
    calls = []
    def validate(db, product_id, selection):
        calls.append((product_id, selection))
        return original(db, product_id, selection)
    monkeypatch.setattr(product_options_service, "validate_product_selection", validate)
    with menu_db.sessions() as db:
        result = cart_service.validate_cart(db, payload)
        assert result.items[0].selection.unit_total == Decimal("89.00")
        assert isinstance(result.items[0].selection.unit_total, Decimal)
        assert not db.new and not db.dirty and not db.deleted
    assert calls == [(row.product_id, row) for row in payload.items]
    assert [row.quantity for row in result.items] == [2, 1]


def test_cart_revalidates_current_unit_prices(menu_client, menu_db, detail_catalog):
    c = detail_catalog
    payload = {"items": [item(c.product, quantity=7)]}
    assert menu_client.post(PATH, json=payload).json()["items"][0]["selection"]["unit_total"] == "89.00"
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[0].product_variant_id).price = Decimal("101.25")
    response = menu_client.post(PATH, json=payload)
    assert response.status_code == 200
    assert response.json()["items"][0]["selection"]["unit_total"] == "101.25"
    assert response.json()["items"][0]["quantity"] == 7


@pytest.mark.parametrize("error_type,status", [
    (menu_service.MenuUnavailableError, 503), (menu_service.MenuReadError, 500), (RuntimeError, 500),
])
def test_cart_catalog_errors_are_sanitized(api_app, monkeypatch, caplog, error_type, status):
    def fail(*args):
        raise error_type("synthetic-private-connection-details")
    monkeypatch.setattr(product_options_service, "validate_product_selection", fail)
    api_app.dependency_overrides[get_db] = lambda: Mock()
    with TestClient(api_app) as client:
        response = client.post(PATH, json={"items": [{"product_id": 1, "product_variant_id": 1, "quantity": 1}]})
    assert response.status_code == status
    assert "synthetic-private-connection-details" not in response.text + caplog.text


@pytest.mark.parametrize("mode", ["public", "auth", "legacy_admin"])
def test_cart_json_is_available_to_react_without_broadening_other_cors_routes(mode):
    origin = "http://localhost:5173"
    if mode == "public":
        config = cloud_settings(cors_origins=[origin])
    elif mode == "auth":
        config = auth_config(auth_origins=["http://localhost:3000"], cors_origins=[origin])
    else:
        config = Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
                          admin_enabled=True, admin_api_key="synthetic-local-key-" * 3, cors_origins=[origin])
    app = create_app(config)
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54350)) as client:
        preflight = client.options(PATH, headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                  "Access-Control-Request-Headers": "content-type"})
        assert preflight.status_code == 200
        response = client.post(PATH, headers={"Origin": origin}, json={"items": []})
        assert response.status_code == 200
        for result in (preflight, response):
            assert result.headers["access-control-allow-origin"] == origin
            assert "access-control-allow-credentials" not in result.headers
        assert client.options(PATH, headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}).status_code == 400
        if mode != "legacy_admin":
            assert client.options("/api/menu", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}).status_code == 400
        if mode == "auth":
            assert client.options("/api/auth/login", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}).status_code == 400
        if mode == "legacy_admin":
            assert client.get("/api/admin/tags").status_code == 401
            assert client.options("/api/menu", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                       "Access-Control-Request-Headers": "x-admin-key"}).status_code == 200

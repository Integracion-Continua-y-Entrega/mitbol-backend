"""HU-017 sobre el catálogo PostgreSQL aislado usado por el menú público."""

from decimal import Decimal, localcontext
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from app.db.database import get_db
from app.models import Product, ProductComplement, ProductVariant
from app.schemas.product_options import ProductSelectionInput
from app.services import menu_service, product_options_service
from test_menu import add_product, api_app, capture_sql, detail_catalog, menu_client, menu_db


def selection_path(catalog):
    return f"/api/menu/{catalog.product.product_id}/selection"


def selection_params(catalog, complements=()):
    return {"product_variant_id": catalog.product.variants[0].product_variant_id,
            "complement_ids": list(complements)}


@pytest.mark.parametrize("keys,extra,total", [
    ([], "0.00", "89.00"),
    (["free"], "0.00", "89.00"),
    (["paid"], "15.50", "104.50"),
    (["free", "paid"], "15.50", "104.50"),
    (["paid", "sauce"], "18.50", "107.50"),
])
def test_valid_selection_uses_catalog_prices(menu_client, detail_catalog, keys, extra, total):
    c = detail_catalog
    options = dict(free=c.shared.complements[0], paid=c.shared.complements[1], sauce=c.exclusive.complements[0])
    response = menu_client.get(selection_path(c), params=selection_params(
        c, [options[key].product_complement_id for key in keys]))
    assert response.status_code == 200, response.text
    assert "authorization" not in response.request.headers and "cookie" not in response.request.headers
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body == {
        "currency": "MXN", "product_id": c.product.product_id, "product_name": c.product.name,
        "product_variant_id": c.product.variants[0].product_variant_id,
        "variant_name": c.product.variants[0].name, "base_price": "89.00",
        "complements": [{
            "product_modifier_id": options[key].product_modifier_id,
            "product_complement_id": options[key].product_complement_id,
            "name": options[key].name, "price": str(options[key].price),
        } for key in keys],
        "complements_total": extra, "unit_total": total,
    }


@pytest.mark.parametrize("condition,status", [
    ("missing_product", 404), ("missing_variant", 422), ("foreign_variant", 422),
    ("missing_complement", 422), ("foreign_group", 422), ("other_variant_group", 422),
])
def test_selection_rejects_missing_or_incompatible_options(menu_client, menu_db, detail_catalog, condition, status):
    c = detail_catalog
    path, params = selection_path(c), selection_params(c)
    if condition == "missing_product":
        path = "/api/menu/2147483647/selection"
    elif condition == "missing_variant":
        params["product_variant_id"] = 2147483647
    elif condition == "foreign_variant":
        params["product_variant_id"] = c.other.variants[0].product_variant_id
    elif condition == "other_variant_group":
        with menu_db.sessions.begin() as db:
            db.get(ProductVariant, c.product.variants[1].product_variant_id).is_available = True
        params["product_variant_id"] = c.product.variants[1].product_variant_id
        params["complement_ids"] = [c.exclusive.complements[0].product_complement_id]
    else:
        params["complement_ids"] = [2147483647 if condition == "missing_complement"
                                     else c.foreign.complements[0].product_complement_id]
    assert menu_client.get(path, params=params).status_code == status


@pytest.mark.parametrize("target,field,status", [
    ("product", "is_active", 404), ("product", "is_available", 409),
    ("variant", "is_available", 409), ("variant", "is_commercially_available", 409),
    ("complement", "is_active", 409),
])
def test_selection_rechecks_availability(menu_client, menu_db, detail_catalog, target, field, status):
    c = detail_catalog
    option = c.shared.complements[1]
    rows = {"product": (Product, c.product.product_id),
            "variant": (ProductVariant, c.product.variants[0].product_variant_id),
            "complement": (ProductComplement, option.product_complement_id)}
    with menu_db.sessions.begin() as db:
        setattr(db.get(*rows[target]), field, False)
    assert menu_client.get(selection_path(c), params=selection_params(c, [option.product_complement_id])).status_code == status


@pytest.mark.parametrize("product_id,params", [
    (0, {"product_variant_id": 1}),
    (1, {}), (1, {"product_variant_id": 0}), (1, {"product_variant_id": "true"}),
    (1, {"product_variant_id": "1.5"}),
    (1, {"product_variant_id": 1, "complement_ids": [0]}),
    (1, {"product_variant_id": 1, "complement_ids": [-1]}),
    (1, {"product_variant_id": 1, "complement_ids": ["invalid"]}),
    (1, {"product_variant_id": 1, "complement_ids": [2, 2]}),
    (1, {"product_variant_id": 1, "price": "0.01"}),
    (1, {"product_variant_id": 1, "unit_total": "0.01"}),
])
def test_invalid_selection_contract_is_rejected_before_query(api_app, product_id, params):
    db = Mock()
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        assert client.get(f"/api/menu/{product_id}/selection", params=params).status_code == 422
    assert db.mock_calls == []


@pytest.mark.parametrize("base,extra,expected", [
    ("0.10", "0.20", "0.30"),
    ("0.00", "0.00", "0.00"),
    ("123456789012345678901234567890.1234567890123456789", "0.0000000000000000001",
     "123456789012345678901234567890.123456789012345679"),
])
def test_selection_uses_current_prices_without_decimal_rounding(menu_client, menu_db, detail_catalog, base, extra, expected):
    c = detail_catalog
    option = c.shared.complements[1]
    params = selection_params(c, [option.product_complement_id])
    assert menu_client.get(selection_path(c), params=params).json()["unit_total"] == "104.50"
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, params["product_variant_id"]).price = Decimal(base)
        db.get(ProductComplement, option.product_complement_id).price = Decimal(extra)
    with localcontext() as context:
        context.prec = 4
        response = menu_client.get(selection_path(c), params=params)
    assert response.status_code == 200, response.text
    assert response.json()["unit_total"] == expected


def test_selection_service_preserves_decimals_and_only_reads(menu_db, detail_catalog):
    c = detail_catalog
    selection = ProductSelectionInput(**selection_params(c, [c.shared.complements[1].product_complement_id]))
    with menu_db.sessions() as db, capture_sql(menu_db.connection) as statements:
        result = product_options_service.validate_product_selection(db, c.product.product_id, selection)
        assert isinstance(result.unit_total, Decimal) and result.unit_total == Decimal("104.50")
        assert isinstance(result.complements_total, Decimal)
        assert isinstance(result.complements[0].price, Decimal)
        assert not db.new and not db.dirty and not db.deleted
    assert not any(sql.lstrip().upper().startswith(
        ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE", "COMMIT")) for sql in statements)


@pytest.mark.parametrize("error_type,status", [
    (menu_service.MenuUnavailableError, 503), (menu_service.MenuReadError, 500), (RuntimeError, 500),
])
def test_selection_catalog_errors_are_sanitized(api_app, monkeypatch, caplog, error_type, status):
    def fail(*args):
        raise error_type("synthetic-private-connection-details")
    monkeypatch.setattr(menu_service, "get_product_detail", fail)
    api_app.dependency_overrides[get_db] = lambda: Mock()
    with TestClient(api_app) as client:
        response = client.get("/api/menu/1/selection", params={"product_variant_id": 1})
    assert response.status_code == status
    assert "synthetic-private-connection-details" not in response.text + caplog.text

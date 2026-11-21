"""HU-019: importes vigentes mediante HU-016/HU-017, sin registrar pedidos."""

from decimal import Decimal, localcontext
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from app.db.database import get_db
from app.models import ProductComplement, ProductVariant
from app.schemas.cart import CartValidationInput
from app.services import cart_service, order_summary_service
from test_cart import item
from test_menu import add_product, api_app, capture_sql, detail_catalog, menu_client, menu_db


PATH = "/api/cart/summary"


def test_summary_includes_selected_details_and_all_line_totals_without_writes(menu_client, menu_db, detail_catalog):
    c = detail_catalog
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[1].product_variant_id).is_available = True
    payload = {"items": [
        item(c.product, quantity=2, complements=[option.product_complement_id for option in c.shared.complements[:2]]),
        item(c.other, quantity=3, complements=[c.foreign.complements[0].product_complement_id]),
        item(c.product, variant_index=1),
        item(c.product, quantity=4),
    ]}
    with capture_sql(menu_db.connection) as statements:
        response = menu_client.post(PATH, json=payload)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" not in response.request.headers and "authorization" not in response.request.headers
    body = response.json()
    assert body["currency"] == "MXN"
    assert [row["quantity"] for row in body["items"]] == [2, 3, 1, 4]
    assert [row["subtotal"] for row in body["items"]] == ["209.00", "159.00", "129.00", "356.00"]
    assert [row["selection"]["unit_total"] for row in body["items"]] == ["104.50", "53.00", "129.00", "89.00"]
    selection = body["items"][0]["selection"]
    assert selection["product_id"] == c.product.product_id
    assert selection["product_name"] == c.product.name
    assert selection["product_variant_id"] == c.product.variants[0].product_variant_id
    assert selection["variant_name"] == c.product.variants[0].name
    assert selection["base_price"] == "89.00" and selection["complements_total"] == "15.50"
    assert selection["complements"] == [
        {"product_modifier_id": c.shared.product_modifier_id, "product_complement_id": option.product_complement_id,
         "name": option.name, "price": price}
        for option, price in zip(c.shared.complements[:2], ["0.00", "15.50"])
    ]
    assert body["subtotal"] == body["total"] == "853.00"
    assert body["discount_total"] == "0.00" and body["discounts_status"] == "not_integrated"
    assert not any(sql.lstrip().upper().startswith(
        ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE", "COMMIT")) for sql in statements)


def test_empty_summary_is_zero_without_queries(api_app):
    db = Mock()
    api_app.dependency_overrides[get_db] = lambda: db
    with TestClient(api_app) as client:
        response = client.post(PATH, json={"items": []})
    assert response.status_code == 200
    assert response.json() == {"currency": "MXN", "items": [], "subtotal": "0.00", "discount_total": "0.00",
                               "total": "0.00", "discounts_status": "not_integrated", "applied_discounts": [], "pending_discounts": []}
    assert db.mock_calls == []


def test_summary_revalidates_variant_and_complement_prices_on_each_request(menu_client, menu_db, detail_catalog):
    c = detail_catalog
    option_id = c.shared.complements[1].product_complement_id
    payload = {"items": [item(c.product, quantity=3, complements=[option_id])]}
    assert menu_client.post(PATH, json=payload).json()["total"] == "313.50"
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[0].product_variant_id).price = Decimal("90.10")
        db.get(ProductComplement, option_id).price = Decimal("0.20")
    response = menu_client.post(PATH, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["selection"]["unit_total"] == "90.30"
    assert response.json()["subtotal"] == response.json()["total"] == "270.90"


@pytest.mark.parametrize("base,extra,quantity,other_price,other_quantity,line_subtotal,total", [
    ("0.10", "0.20", 3, "0.20", 4, "0.90", "1.70"),
    ("0.00", "0.00", 10, "0.00", 1, "0.00", "0.00"),
    ("0.0001", "0.0002", 3, "0.0001", 1, "0.0009", "0.001"),
    ("123456789012345678901234567890.1234567890123456789", "0.0000000000000000001", 3, "0.01", 3,
     "370370367037037036703703703670.370370367037037037", "370370367037037036703703703670.400370367037037037"),
    ("0.01", "0.00", 123456789012345678901234567890, "0.01", 1,
     "1234567890123456789012345678.90", "1234567890123456789012345678.91"),
])
def test_summary_uses_exact_decimals_even_with_low_context_precision(
    menu_client, menu_db, detail_catalog, base, extra, quantity, other_price, other_quantity, line_subtotal, total,
):
    c = detail_catalog
    option_id = c.shared.complements[1].product_complement_id
    with menu_db.sessions.begin() as db:
        db.get(ProductVariant, c.product.variants[0].product_variant_id).price = Decimal(base)
        db.get(ProductComplement, option_id).price = Decimal(extra)
        db.get(ProductVariant, c.other.variants[0].product_variant_id).price = Decimal(other_price)
    payload = CartValidationInput(items=[item(c.product, quantity=quantity, complements=[option_id]),
                                         item(c.other, quantity=other_quantity)])
    with localcontext() as context, menu_db.sessions() as db:
        context.prec = 2
        result = order_summary_service.summarize_cart(db, payload)
        assert isinstance(result.total, Decimal) and isinstance(result.items[0].subtotal, Decimal)
        assert result.items[0].subtotal == Decimal(line_subtotal)
        assert result.subtotal == result.total == Decimal(total)
        assert context.prec == 2
    response = menu_client.post(PATH, json=payload.model_dump())
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["subtotal"] == line_subtotal
    assert response.json()["subtotal"] == response.json()["total"] == total


def test_summary_reuses_cart_validation_once(menu_db, detail_catalog, monkeypatch):
    c = detail_catalog
    payload = CartValidationInput(items=[item(c.product, quantity=2), item(c.other)])
    validate = Mock(wraps=cart_service.validate_cart)
    monkeypatch.setattr(cart_service, "validate_cart", validate)
    with menu_db.sessions() as db:
        result = order_summary_service.summarize_cart(db, payload)
        validate.assert_called_once_with(db, payload)
        assert result.total == Decimal("223.00")
        assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("price", ["-0.01", "NaN", "Infinity"])
@pytest.mark.parametrize("source", ["variant", "complement"])
def test_invalid_stored_prices_never_produce_an_amount(menu_client, menu_db, detail_catalog, price, source):
    c = detail_catalog
    option_id = c.shared.complements[1].product_complement_id
    with menu_db.sessions.begin() as db:
        row = (db.get(ProductVariant, c.product.variants[0].product_variant_id) if source == "variant"
               else db.get(ProductComplement, option_id))
        row.price = Decimal(price)
    response = menu_client.post(PATH, json={"items": [item(c.product, complements=[option_id])]})
    assert response.status_code == 500
    assert set(response.json()) == {"detail"}
    assert "subtotal" not in response.json() and "total" not in response.json()
    assert response.headers["cache-control"] == "no-store"

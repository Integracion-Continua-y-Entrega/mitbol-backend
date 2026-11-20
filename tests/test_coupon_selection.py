"""HU-020 con datos exclusivamente sintéticos en PostgreSQL desechable."""

from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import delete, event, select, update

from app.models import (
    CustomerCoupon, Discount, Order, Product, ProductComplement, ProductModifier,
    ProductModifierAssignment, ProductVariant, RestaurantOrderSettings,
)
from app.schemas.coupons import CouponSelectionInput
from app.services import coupon_service
from test_auth_api import backend as auth_backend, csrf_headers, login, ORIGIN, password_hash
from test_auth_persistence import auth_database, config_for
from test_cart import item


PATH = "/api/auth/coupons"
SELECT = PATH + "/select"


@pytest.fixture
def backend(auth_backend):
    b = auth_backend
    with b.sessions.kw["bind"].begin() as connection:
        command.upgrade(config_for(connection), "20261119_0012")
    with b.sessions.begin() as db:
        products = []
        for price in ("50.00", "20.00"):
            product = Product(loyverse_id=uuid4().hex, name="Producto sintético", synced_at=b.now.value)
            product.variants = [ProductVariant(loyverse_id=uuid4().hex, name="Única", price=Decimal(price),
                                               is_commercially_available=True, synced_at=b.now.value)]
            db.add(product)
            products.append(product)
        modifier = ProductModifier(loyverse_id=uuid4().hex, name="Extras sintéticos", display_order=0, synced_at=b.now.value)
        complement = ProductComplement(modifier=modifier, loyverse_id=uuid4().hex, name="Extra sintético",
                                       price=Decimal("5.00"), display_order=0, synced_at=b.now.value)
        discount = Discount(loyverse_id=uuid4().hex, name="Beneficio sintético", discount_type="FIXED_AMOUNT",
                            value=Decimal("25.00"), scope="unknown", is_active=True)
        db.add_all([modifier, complement, discount])
        db.flush()
        db.add(ProductModifierAssignment(product_variant_id=products[0].variants[0].product_variant_id,
                                         product_modifier_id=modifier.product_modifier_id))
        coupons = [CustomerCoupon(account_id=b.ids[role][0], name=f"Cupón sintético {role}",
            description="Solo escenario automatizado; no es una recompensa real del restaurante.",
            discount_id=discount.discount_id, starts_at=b.now.value - timedelta(days=1),
            expires_at=b.now.value + timedelta(days=1),
            conditions={"scope": "order", "minimum_subtotal": "150.00", "minimum_quantity": 5})
            for role in ("customer", "admin")]
        db.add_all(coupons)
        db.get(RestaurantOrderSettings, 1).accepting_orders = True
    b.products, b.complement, b.modifier, b.discount = products, complement, modifier, discount
    b.coupon, b.foreign_coupon = coupons
    b.selection = {"customer_coupon_id": b.coupon.customer_coupon_id, "modality": "dine_in", "cart": {"items": [
        item(products[0], quantity=2, complements=[complement.product_complement_id]), item(products[1], quantity=3)]}}
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(Order))
            db.execute(delete(CustomerCoupon))
            db.execute(delete(Discount))
            db.execute(delete(ProductModifierAssignment))
            db.execute(delete(ProductComplement))
            db.execute(delete(ProductModifier))
            db.execute(delete(ProductVariant))
            db.execute(delete(Product))


def select_coupon(b, *, changes=None, headers=None):
    return b.client.post(SELECT, headers=headers, json=b.selection | (changes or {}))


def coupon_state(b):
    with b.sessions() as db:
        return db.execute(select(CustomerCoupon.__table__).order_by(CustomerCoupon.customer_coupon_id)).all()


@pytest.mark.parametrize("modality", ["dine_in", "pickup"])
def test_owned_wallet_and_selection_calculate_fixed_benefit_without_reserving_or_consuming(backend, modality):
    b = backend
    signed_in = login(b, "customer")
    before, statements = coupon_state(b), []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = b.service.sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        wallet = b.client.get(PATH)
        for _ in range(2):
            response = select_coupon(b, changes={"modality": modality}, headers=csrf_headers(signed_in))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["eligible"] and body["selected_coupon_id"] == b.coupon.customer_coupon_id
            assert body["benefit_amount"] == "25.00" and body["calculation_status"] == "calculated"
            assert body["summary"]["subtotal"] == body["summary"]["total"] == "170.00"
            assert body["summary"]["discount_total"] == "0.00" and body["summary"]["discounts_status"] == "not_integrated"
            assert body["order_integration_status"] == "not_integrated" and body["preliminary"]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert wallet.status_code == 200 and len(wallet.json()["coupons"]) == 1
    coupon = wallet.json()["coupons"][0]
    assert coupon["customer_coupon_id"] == b.coupon.customer_coupon_id and coupon["available"]
    assert coupon["conditions"]["minimum_subtotal"] == "150.00"
    assert "account_id" not in coupon and "email" not in coupon and "loyverse_id" not in coupon
    assert wallet.headers["cache-control"] == response.headers["cache-control"] == "no-store"
    assert coupon_state(b) == before
    assert not any('"CustomerCoupons"' in sql and sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")) for sql in statements)


def test_ownership_missing_ids_empty_wallet_and_pagination(backend):
    b = backend
    signed_in = login(b, "customer")
    responses = [select_coupon(b, changes={"customer_coupon_id": identifier}, headers=csrf_headers(signed_in))
                 for identifier in (b.foreign_coupon.customer_coupon_id, 999999999)]
    assert [r.status_code for r in responses] == [404, 404] and responses[0].json() == responses[1].json()
    assert b.foreign_coupon.name not in responses[0].text
    assert b.client.get(PATH, params={"account_id": b.ids["admin"][0]}).json()["coupons"][0]["customer_coupon_id"] == b.coupon.customer_coupon_id
    assert b.client.get(PATH, params={"limit": 1, "offset": 1}).json() == {"coupons": []}
    assert b.client.get(PATH, params={"limit": 101}).status_code == 422
    login(b, "staff")
    assert b.client.get(PATH).json() == {"coupons": []}


@pytest.mark.parametrize("method,path", [("GET", PATH), ("POST", SELECT)])
def test_guests_cannot_list_or_select_personal_coupons(backend, method, path):
    response = backend.client.request(method, path, headers={"Origin": ORIGIN},
                                      **({"json": backend.selection} if method == "POST" else {}))
    assert response.status_code == 401


def test_csrf_origins_and_credentialed_cors_are_preserved(backend):
    b = backend
    signed_in = login(b, "customer")
    for headers in ({"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "0" * 64},
                    {**csrf_headers(signed_in), "Origin": "https://evil.invalid"}):
        assert select_coupon(b, headers=headers).status_code == 403
    preflight = b.client.options(SELECT, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token"})
    assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize("condition", ["future", "expired", "reserved", "redeemed", "expired_status"])
def test_validity_and_availability_reject_selection_without_changing_status(backend, condition):
    b = backend
    signed_in = login(b, "customer")
    with b.sessions.begin() as db:
        coupon = db.get(CustomerCoupon, b.coupon.customer_coupon_id)
        if condition == "future":
            coupon.starts_at = b.now.value + timedelta(hours=1)
        elif condition == "expired":
            coupon.expires_at = b.now.value
        else:
            coupon.status = "expired" if condition == "expired_status" else condition
    before = coupon_state(b)
    body = select_coupon(b, headers=csrf_headers(signed_in)).json()
    assert not body["eligible"] and body["selected_coupon_id"] is None and body["benefit_amount"] is None
    assert body["calculation_status"] == "not_eligible" and coupon_state(b) == before


@pytest.mark.parametrize("conditions,reason", [
    ({"scope": "order", "minimum_subtotal": "170.01"}, "minimum_subtotal_not_met"),
    ({"scope": "order", "minimum_quantity": 6}, "minimum_quantity_not_met"),
    ({"scope": "order", "modalities": ["pickup"]}, "modality_not_allowed"),
    (None, "conditions_pending"), ({}, "conditions_pending"),
    ({"scope": "order", "unknown_business_rule": True}, "conditions_pending"),
    ({"scope": "products"}, "conditions_pending"),
    ({"scope": "order", "minimum_subtotal": "-1"}, "conditions_pending"),
])
def test_real_conditions_are_checked_and_unknown_conditions_fail_closed(backend, conditions, reason):
    b = backend
    signed_in = login(b, "customer")
    with b.sessions.begin() as db:
        db.get(CustomerCoupon, b.coupon.customer_coupon_id).conditions = conditions
    body = select_coupon(b, headers=csrf_headers(signed_in)).json()
    assert not body["eligible"] and body["reason"] == reason
    assert body["selected_coupon_id"] is None and body["benefit_amount"] is None
    assert body["calculation_status"] == ("rules_pending" if reason == "conditions_pending" else "not_eligible")


@pytest.mark.parametrize("kind,value,reason", [
    ("FIXED_PERCENT", "10", "rounding_rules_pending"),
    ("VARIABLE_AMOUNT", None, "benefit_rules_pending"),
    ("DISCOUNT_BY_POINTS", None, "benefit_rules_pending"),
    ("FIXED_AMOUNT", "NaN", "benefit_pending"), ("FIXED_AMOUNT", "Infinity", "benefit_pending"),
    ("FIXED_AMOUNT", "-5", "benefit_pending"), ("FIXED_AMOUNT", "0", "benefit_pending"),
    ("FIXED_AMOUNT", "170.01", "benefit_exceeds_subtotal"),
])
def test_incomplete_or_invalid_benefits_never_produce_fictitious_or_negative_amounts(backend, kind, value, reason):
    b = backend
    signed_in = login(b, "customer")
    with b.sessions.begin() as db:
        discount = db.get(Discount, b.discount.discount_id)
        discount.discount_type, discount.value = kind, Decimal(value) if value is not None else None
    response = select_coupon(b, headers=csrf_headers(signed_in))
    assert response.status_code == 200, response.text
    body = response.json()
    assert not body["eligible"] and body["reason"] == reason and body["benefit_amount"] is None
    assert body["summary"]["total"] == "170.00"


@pytest.mark.parametrize("condition", ["inactive", "missing"])
def test_discount_must_exist_and_remain_active(backend, condition):
    b = backend
    signed_in = login(b, "customer")
    with b.sessions.begin() as db:
        if condition == "inactive":
            db.get(Discount, b.discount.discount_id).is_active = False
        else:
            db.get(CustomerCoupon, b.coupon.customer_coupon_id).discount_id = None
    body = select_coupon(b, headers=csrf_headers(signed_in)).json()
    assert not body["eligible"] and body["benefit_amount"] is None
    assert body["reason"] == ("discount_unavailable" if condition == "inactive" else "benefit_pending")


@pytest.mark.parametrize("field,value", [
    ("customer_coupon_id", [1, 2]), ("customer_coupon_id", 0), ("customer_coupon_id", True),
    ("customer_coupon_ids", [1, 2]), ("benefit_amount", "1000"), ("discount_total", "1000"),
    ("account_id", 1), ("modality", "delivery"),
    ("cart", {"items": [{"product_id": 1, "product_variant_id": 1, "quantity": 0}]}),
])
def test_one_coupon_limit_and_server_owned_prices_and_identity(backend, field, value):
    b = backend
    signed_in = login(b, "customer")
    before = coupon_state(b)
    assert select_coupon(b, changes={field: value}, headers=csrf_headers(signed_in)).status_code == 422
    assert coupon_state(b) == before


@pytest.mark.parametrize("condition,status", [("unavailable_product", 409), ("wrong_variant", 422), ("wrong_complement", 422)])
def test_selection_reuses_cart_availability_and_option_validation(backend, condition, status):
    b = backend
    signed_in = login(b, "customer")
    if condition == "unavailable_product":
        with b.sessions.begin() as db:
            db.get(Product, b.products[0].product_id).is_available = False
    elif condition == "wrong_variant":
        b.selection["cart"]["items"][0]["product_variant_id"] = b.products[1].variants[0].product_variant_id
    else:
        b.selection["cart"]["items"][1]["complement_ids"] = [b.complement.product_complement_id]
    response = select_coupon(b, headers=csrf_headers(signed_in))
    assert response.status_code == status and response.json()["detail"]["item_index"] == (1 if condition == "wrong_complement" else 0)


def test_exact_decimal_preview_and_current_prices_are_revalidated(backend):
    b = backend
    with b.sessions.begin() as db:
        db.get(CustomerCoupon, b.coupon.customer_coupon_id).conditions = {"scope": "order"}
        db.get(Discount, b.discount.discount_id).value = Decimal("0.10")
        for product in b.products:
            db.get(ProductVariant, product.variants[0].product_variant_id).price = Decimal("0.20")
        db.get(ProductComplement, b.complement.product_complement_id).price = Decimal("0.10")
    data = CouponSelectionInput(**b.selection)
    with localcontext() as context, b.sessions() as db:
        context.prec = 2
        result = coupon_service.select_coupon(db, b.ids["customer"][0], data, now=b.now.value)
        assert result.benefit_amount == Decimal("0.10") and result.summary.total == Decimal("1.20")
        assert isinstance(result.benefit_amount, Decimal) and context.prec == 2
    with b.sessions.begin() as db:
        db.get(Discount, b.discount.discount_id).value = Decimal("2.00")
    signed_in = login(b, "customer")
    assert select_coupon(b, headers=csrf_headers(signed_in)).json()["reason"] == "benefit_exceeds_subtotal"


def test_empty_cart_is_not_eligible_and_existing_order_submission_does_not_apply_or_consume_coupon(backend):
    b = backend
    signed_in = login(b, "customer")
    assert select_coupon(b, changes={"cart": {"items": []}}, headers=csrf_headers(signed_in)).json()["reason"] == "empty_cart"
    before = coupon_state(b)
    assert select_coupon(b, headers=csrf_headers(signed_in)).json()["eligible"]
    body = {"cart": b.selection["cart"], "data": {"modality": "dine_in", "name": "Cliente sintético"}}
    response = b.client.post("/api/orders", json=body, headers=csrf_headers(signed_in) | {"Idempotency-Key": str(uuid4())})
    assert response.status_code == 201, response.text
    assert response.json()["total"] == "170.00" and response.json()["discount_total"] == "0.00"
    with b.sessions() as db:
        order = db.scalar(select(Order))
        assert order.total == Decimal("170.00") and order.discount_total == 0
    assert coupon_state(b) == before
    response = b.client.post("/api/orders", json=body | {"customer_coupon_id": b.coupon.customer_coupon_id},
                            headers=csrf_headers(signed_in) | {"Idempotency-Key": str(uuid4())})
    assert response.status_code == 422

"""HU-020: cálculo compartido e historial; sólo PostgreSQL desechable."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.models import CommercialDiscountAssignment, CustomerCoupon, Discount, Order, OrderDiscountSnapshot, ProductVariant
from app.services.order_tracking_service import historical_summary
from test_auth_api import csrf_headers, login
from test_coupon_selection import auth_backend, auth_database, backend, coupon_state, password_hash, select_coupon


def summary(b, headers, **changes):
    return b.client.post("/api/cart/summary", headers=headers,
        json=b.selection["cart"] | {"customer_coupon_id":b.coupon.customer_coupon_id,"modality":"dine_in"} | changes)


def order(b, headers, *, key=None, modality="dine_in", **changes):
    data = {"modality":modality,"name":"Cliente sintético"}
    if modality == "pickup":
        data["phone"] = "+522281234567"
    return b.client.post("/api/orders", headers=headers | {"Idempotency-Key":str(key or uuid4())},
        json={"cart":b.selection["cart"],"data":data,"customer_coupon_id":b.coupon.customer_coupon_id} | changes)


@pytest.mark.parametrize("modality", ["dine_in","pickup"])
def test_selection_summary_order_and_historical_coupon_match_without_consumption(backend, modality):
    b = backend
    headers = csrf_headers(login(b,"customer"))
    before = coupon_state(b)
    selected = select_coupon(b,headers=headers,changes={"modality":modality})
    response = summary(b,headers,modality=modality)
    assert response.status_code == 200, response.text
    expected = response.json()
    assert selected.json()["summary"] == expected
    assert expected["coupon"]["selected_coupon_id"] == b.coupon.customer_coupon_id
    assert expected["coupon"]["benefit_amount"] == "25.00" and expected["total"] == "145.00"
    key = uuid4()
    created = order(b,headers,key=key,modality=modality)
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert {name:receipt[name] for name in expected} == expected
    assert coupon_state(b) == before
    with b.sessions() as db:
        stored = db.get(Order,UUID(receipt["order_id"]))
        snapshot = db.get(OrderDiscountSnapshot,stored.order_id)
        assert stored.account_id == b.ids["customer"][0] and stored.total == Decimal("145.00")
        assert snapshot.calculation["coupon"] == expected["coupon"]
    with b.sessions.begin() as db:
        db.get(CustomerCoupon,b.coupon.customer_coupon_id).expires_at = b.now.value
        db.get(Discount,b.discount.discount_id).value = Decimal("80")
        db.get(ProductVariant,b.products[0].variants[0].product_variant_id).price = Decimal("99")
    assert order(b,headers,key=key,modality=modality).json() == receipt
    with b.sessions() as db:
        history = historical_summary(db.get(Order,UUID(receipt["order_id"])))
        assert history.coupon.benefit_amount == Decimal("25.00") and history.total == Decimal("145.00")


@pytest.mark.parametrize("target", ["foreign","missing"])
def test_summary_and_submission_do_not_disclose_or_apply_other_accounts_coupons(backend, target):
    b = backend
    headers = csrf_headers(login(b,"customer"))
    identifier = b.foreign_coupon.customer_coupon_id if target == "foreign" else 999999999
    for response in (summary(b,headers,customer_coupon_id=identifier),order(b,headers,customer_coupon_id=identifier)):
        assert response.status_code == 404 and b.foreign_coupon.name not in response.text
    with b.sessions() as db:
        assert not db.scalars(select(Order)).all()


@pytest.mark.parametrize("condition,reason", [("expired","outside_validity"),("reserved","not_available"),
                                             ("modality","modality_not_allowed")])
def test_order_revalidates_coupon_after_successful_selection(backend, condition, reason):
    b = backend
    headers = csrf_headers(login(b,"customer"))
    assert summary(b,headers).json()["coupon"]["eligible"]
    with b.sessions.begin() as db:
        coupon = db.get(CustomerCoupon,b.coupon.customer_coupon_id)
        if condition == "expired":
            coupon.expires_at = b.now.value
        elif condition == "reserved":
            coupon.status = "reserved"
        else:
            coupon.conditions = {"scope":"order","modalities":["pickup"]}
    response = summary(b,headers)
    assert response.status_code == 200 and response.json()["coupon"]["reason"] == reason
    assert response.json()["total"] == "170.00"
    rejected = order(b,headers)
    assert rejected.status_code == 409 and rejected.json()["detail"]["coupon"]["reason"] == reason
    with b.sessions() as db:
        assert not db.scalars(select(Order)).all() and not db.scalars(select(OrderDiscountSnapshot)).all()


def commercial(b, *, kind="FIXED_PERCENT", compatible=True):
    with b.sessions.begin() as db:
        record = Discount(loyverse_id=uuid4().hex,name="Comercial sintético",discount_type=kind,
                          value=Decimal("10") if kind == "FIXED_PERCENT" else Decimal("5"),scope="unknown",is_active=True)
        db.add(record)
        db.flush()
        db.add_all([CommercialDiscountAssignment(discount_id=record.discount_id,product_id=product.product_id,
            conditions={"include_complements":True,"compatible_discount_ids":[b.discount.discount_id] if compatible else []})
            for product in b.products])
    return record


@pytest.mark.parametrize("coupon_kind,total,benefit", [("FIXED_PERCENT","148.00","17.00"),
                                                       ("FIXED_AMOUNT","128.00","25.00")])
def test_compatible_coupon_and_commercial_discounts_share_ordering_and_totals(backend, coupon_kind, total, benefit):
    b = backend
    other = commercial(b,kind="FIXED_AMOUNT" if coupon_kind == "FIXED_PERCENT" else "FIXED_PERCENT")
    with b.sessions.begin() as db:
        db.get(CustomerCoupon,b.coupon.customer_coupon_id).conditions = {"scope":"order","compatible_discount_ids":[other.discount_id]}
        discount = db.get(Discount,b.discount.discount_id)
        discount.discount_type = coupon_kind
        discount.value = Decimal("10") if coupon_kind == "FIXED_PERCENT" else Decimal("25")
    headers = csrf_headers(login(b,"customer"))
    response = summary(b,headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == total and result["coupon"]["benefit_amount"] == benefit
    assert [row["discount_type"] for row in result["applied_discounts"]] == ["FIXED_PERCENT","FIXED_AMOUNT"]
    created = order(b,headers)
    assert created.status_code == 201 and created.json()["applied_discounts"] == result["applied_discounts"]
    assert created.json()["total"] == total


@pytest.mark.parametrize("coupon_compatible,commercial_compatible", [(False,True),(True,False),(False,False)])
def test_unknown_or_one_sided_compatibility_never_removes_the_valid_commercial_discount(backend, coupon_compatible, commercial_compatible):
    b = backend
    other = commercial(b,compatible=commercial_compatible)
    with b.sessions.begin() as db:
        db.get(CustomerCoupon,b.coupon.customer_coupon_id).conditions = {"scope":"order",
            **({"compatible_discount_ids":[other.discount_id]} if coupon_compatible else {})}
    headers = csrf_headers(login(b,"customer"))
    result = summary(b,headers).json()
    assert result["coupon"]["reason"] == "commercial_compatibility_pending" and result["coupon"]["benefit_amount"] is None
    assert result["total"] == "153.00" and [row["discount_id"] for row in result["applied_discounts"]] == [other.discount_id]
    assert order(b,headers).status_code == 409


def test_a_discount_already_applied_commercially_is_not_applied_again_as_a_coupon(backend):
    b = backend
    with b.sessions.begin() as db:
        db.add_all([CommercialDiscountAssignment(discount_id=b.discount.discount_id,product_id=product.product_id,
            conditions={"include_complements":True,"compatible_discount_ids":[]}) for product in b.products])
    headers = csrf_headers(login(b,"customer"))
    result = summary(b,headers).json()
    assert result["coupon"]["reason"] == "benefit_already_applied" and result["total"] == "145.00"
    assert result["discount_total"] == "25.00" and len(result["applied_discounts"]) == 1
    assert order(b,headers).status_code == 409


@pytest.mark.parametrize("include,total", [(False,"160.00"),(True,"159.00")])
def test_coupon_product_scope_and_explicit_complement_eligibility(backend, include, total):
    b = backend
    with b.sessions.begin() as db:
        db.get(CustomerCoupon,b.coupon.customer_coupon_id).conditions = {"scope":"products",
            "product_ids":[b.products[0].product_id],"include_complements":include}
        db.get(Discount,b.discount.discount_id).discount_type = "FIXED_PERCENT"
        db.get(Discount,b.discount.discount_id).value = Decimal("10")
    headers = csrf_headers(login(b,"customer"))
    result = summary(b,headers).json()
    assert result["total"] == total and result["coupon"]["eligible"]
    assert [row["item_index"] for row in result["applied_discounts"][0]["allocations"]] == [0]
    assert order(b,headers).json()["total"] == total


@pytest.mark.parametrize("kind,value,expected,total", [("FIXED_PERCENT","10","0.01","0.04"),
    ("VARIABLE_PERCENT","10","0.01","0.04"),("FIXED_AMOUNT","0.005","0.01","0.04"),
    ("FIXED_AMOUNT","999","0.05","0.00")])
def test_coupon_uses_shared_half_up_rounding_and_remaining_amount_caps(backend, kind, value, expected, total):
    b = backend
    with b.sessions.begin() as db:
        db.get(CustomerCoupon,b.coupon.customer_coupon_id).conditions = {"scope":"order"}
        discount = db.get(Discount,b.discount.discount_id)
        discount.discount_type, discount.value = kind, Decimal(value)
        db.get(ProductVariant,b.products[0].variants[0].product_variant_id).price = Decimal("0.05")
    b.selection["cart"]["items"] = [b.selection["cart"]["items"][0] | {"quantity":1,"complement_ids":[]}]
    headers = csrf_headers(login(b,"customer"))
    result = summary(b,headers).json()
    assert result["coupon"]["benefit_amount"] == expected and result["total"] == total
    assert order(b,headers).json()["total"] == total


@pytest.mark.parametrize("change", [{"customer_coupon_id":[1,2]}, {"customer_coupon_ids":[1,2]},
                                  {"benefit_amount":"1000"}, {"account_id":1}])
def test_summary_and_order_accept_only_one_server_validated_coupon(backend, change):
    b = backend
    headers = csrf_headers(login(b,"customer"))
    assert summary(b,headers,**change).status_code == 422
    assert order(b,headers,**change).status_code == 422


def test_guests_keep_ordering_without_coupons_but_cannot_apply_personal_coupons(backend):
    b = backend
    assert summary(b,{"Origin":b.config.auth_origins[0]}).status_code == 401
    assert order(b,{}).status_code == 401
    response = order(b,{},customer_coupon_id=None)
    assert response.status_code == 201 and response.json()["coupon"] is None
    assert response.json()["total"] == "170.00" and response.json()["tracking"]


def test_summary_with_coupon_preserves_csrf_and_requires_modality(backend):
    b = backend
    headers = csrf_headers(login(b,"customer"))
    assert summary(b,{"Origin":headers["Origin"]}).status_code == 403
    assert summary(b,headers,modality=None).status_code == 422


def test_order_recalculates_coupon_from_current_backend_prices(backend):
    b = backend
    with b.sessions.begin() as db:
        db.get(Discount,b.discount.discount_id).discount_type = "FIXED_PERCENT"
        db.get(Discount,b.discount.discount_id).value = Decimal("10")
    headers = csrf_headers(login(b,"customer"))
    assert summary(b,headers).json()["coupon"]["benefit_amount"] == "17.00"
    with b.sessions.begin() as db:
        db.get(ProductVariant,b.products[0].variants[0].product_variant_id).price = Decimal("100")
    created = order(b,headers)
    assert created.status_code == 201, created.text
    assert created.json()["subtotal"] == "270.00" and created.json()["total"] == "243.00"
    assert created.json()["coupon"]["benefit_amount"] == "27.00"


def test_coupon_products_outside_the_cart_are_not_discounted(backend):
    b = backend
    with b.sessions.begin() as db:
        db.get(CustomerCoupon,b.coupon.customer_coupon_id).conditions = {
            "scope":"products","product_ids":[999999999],"include_complements":False}
    headers = csrf_headers(login(b,"customer"))
    result = summary(b,headers).json()
    assert result["coupon"]["reason"] == "products_not_eligible" and result["total"] == "170.00"
    assert order(b,headers).status_code == 409

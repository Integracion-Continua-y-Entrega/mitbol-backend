"""Validación preliminar de cupones; no escribe, reserva ni canjea beneficios."""

from pydantic import ValidationError
from sqlalchemy import select

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, Discount
from app.schemas.coupons import CouponConditions, CouponRead, CouponSelectionResponse
from app.services import order_summary_service
from app.services.discount_rules import DiscountType


def _conditions(coupon):
    try:
        return CouponConditions.model_validate(coupon.conditions)
    except ValidationError:
        # Las condiciones desconocidas nunca se omiten para permitir el uso.
        return None


def _read(coupon, discount, now):
    conditions = _conditions(coupon)
    value = discount.value if discount else None
    safe_value = value if value is not None and value.is_finite() and value >= 0 else None
    return CouponRead(
        customer_coupon_id=coupon.customer_coupon_id, name=coupon.name, description=coupon.description,
        status=coupon.status, starts_at=coupon.starts_at, expires_at=coupon.expires_at,
        available=(coupon.status == "available" and coupon.starts_at <= now < coupon.expires_at),
        conditions=conditions, conditions_status="defined" if conditions else "pending",
        discount_type=discount.discount_type if discount else None, discount_value=safe_value,
    )


def _query(account_id):
    return select(CustomerCoupon, Discount).outerjoin(Discount, CustomerCoupon.discount_id == Discount.discount_id).where(
        CustomerCoupon.account_id == account_id)


def list_coupons(db, account_id, *, now, limit=50, offset=0):
    rows = db.execute(_query(account_id).order_by(CustomerCoupon.customer_coupon_id).limit(limit).offset(offset))
    return [_read(coupon, discount, now) for coupon, discount in rows]


def select_coupon(db, account_id, payload, *, now):
    row = db.execute(_query(account_id).where(CustomerCoupon.customer_coupon_id == payload.customer_coupon_id)).one_or_none()
    if row is None:
        raise AuthError(404, "Cupón no encontrado.", code="COUPON_NOT_FOUND")
    coupon, discount = row
    info = _read(coupon, discount, now)
    summary = order_summary_service.summarize_cart(db, payload.cart)

    def result(reason=None, *, pending=False, amount=None):
        eligible = reason is None
        return CouponSelectionResponse(
            coupon=info, selected_coupon_id=coupon.customer_coupon_id if eligible else None,
            eligible=eligible, reason=reason, benefit_amount=amount, summary=summary,
            calculation_status="rules_pending" if pending else ("calculated" if eligible else "not_eligible"),
        )

    if not info.available:
        return result("not_available" if coupon.status != "available" else "outside_validity")
    if not summary.items:
        return result("empty_cart")
    conditions = info.conditions
    if conditions is None:
        return result("conditions_pending", pending=True)
    if conditions.modalities is not None and payload.modality not in conditions.modalities:
        return result("modality_not_allowed")
    if conditions.minimum_subtotal is not None and summary.subtotal < conditions.minimum_subtotal:
        return result("minimum_subtotal_not_met")
    if conditions.minimum_quantity is not None and sum(item.quantity for item in summary.items) < conditions.minimum_quantity:
        return result("minimum_quantity_not_met")
    if discount is None:
        return result("benefit_pending", pending=True)
    if not discount.is_active:
        return result("discount_unavailable")
    if discount.discount_type != DiscountType.FIXED_AMOUNT.value:
        reason = "rounding_rules_pending" if discount.discount_type == DiscountType.FIXED_PERCENT.value else "benefit_rules_pending"
        return result(reason, pending=True)
    if info.discount_value is None or info.discount_value <= 0:
        return result("benefit_pending", pending=True)
    if summary.applied_discounts:
        return result("commercial_compatibility_pending", pending=True)
    # No inventar un tope ni reducir silenciosamente el beneficio comercial.
    if info.discount_value > summary.subtotal:
        return result("benefit_exceeds_subtotal")
    return result(amount=info.discount_value)

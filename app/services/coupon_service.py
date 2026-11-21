"""Cupones propios calculados con HU-019; no reserva ni canjea beneficios."""

from datetime import datetime, timezone
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import select

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, Discount
from app.schemas.coupons import CouponConditions, CouponRead, CouponSelectionResponse
from app.schemas.order_summary import CouponApplicationResult
from app.services import commercial_discount_service as discounts


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


def _owned_coupon(db, account_id, identifier):
    if account_id is None:
        raise AuthError(401, "Inicia sesión para utilizar un cupón personal.", code="SESSION_INVALID")
    row = db.execute(_query(account_id).where(CustomerCoupon.customer_coupon_id == identifier)).one_or_none()
    if row is None:
        raise AuthError(404, "Cupón no encontrado.", code="COUPON_NOT_FOUND")
    return row


def apply_coupon(db, raw_summary, identifier, *, account_id, modality, now=None):
    row = _owned_coupon(db, account_id, identifier)
    coupon, discount = row
    info = _read(coupon, discount, now or datetime.now(timezone.utc))
    records = discounts.discount_records(db) if raw_summary.items else []
    summary = discounts.calculate(raw_summary, records) if raw_summary.items else raw_summary

    def result(reason=None, *, pending=False, amount=None):
        eligible = reason is None
        application = CouponApplicationResult(
            selected_coupon_id=coupon.customer_coupon_id if eligible else None,
            eligible=eligible, reason=reason, benefit_amount=amount,
            calculation_status="rules_pending" if pending else ("calculated" if eligible else "not_eligible"),
        )
        return summary.model_copy(update={"coupon":application})

    if not info.available:
        return result("not_available" if coupon.status != "available" else "outside_validity")
    if not summary.items:
        return result("empty_cart")
    conditions = info.conditions
    if conditions is None:
        return result("conditions_pending", pending=True)
    if conditions.modalities is not None and modality not in conditions.modalities:
        return result("modality_not_allowed")
    if conditions.minimum_subtotal is not None and summary.subtotal < conditions.minimum_subtotal:
        return result("minimum_subtotal_not_met")
    if conditions.minimum_quantity is not None and sum(item.quantity for item in summary.items) < conditions.minimum_quantity:
        return result("minimum_quantity_not_met")
    if discount is None:
        return result("benefit_pending", pending=True)
    if not discount.is_active:
        return result("discount_unavailable")
    if info.discount_value is None or info.discount_value <= 0:
        return result("benefit_pending", pending=True)
    if any(row.discount_id == discount.discount_id for row in summary.applied_discounts):
        return result("benefit_already_applied")
    product_ids = (conditions.product_ids if conditions.scope == "products"
                   else sorted({item.selection.product_id for item in summary.items}))
    if not any(item.selection.product_id in product_ids for item in summary.items):
        return result("products_not_eligible")
    # order ya declaraba explícitamente el subtotal completo, con complementos.
    include = conditions.include_complements if conditions.include_complements is not None else True
    rules = {"include_complements":include,"compatible_discount_ids":conditions.compatible_discount_ids or []}
    assignments = [SimpleNamespace(product_id=product_id, conditions=rules) for product_id in product_ids]
    combined = discounts.calculate(raw_summary, [row for row in records if row[0].discount_id != discount.discount_id]
                                   + [(discount, assignments)])
    applied = next((row for row in combined.applied_discounts if row.discount_id == discount.discount_id), None)
    if applied is None:
        deferred = next((row for row in combined.pending_discounts if row.discount_id == discount.discount_id), None)
        reason = deferred.reason if deferred else "no_remaining_eligible_amount"
        if reason == "compatibility_pending":
            reason = "commercial_compatibility_pending"
        return result(reason, pending=deferred is not None)
    summary = combined
    return result(amount=applied.amount)


def select_coupon(db, account_id, payload, *, now):
    from app.services import order_summary_service
    coupon, discount = _owned_coupon(db, account_id, payload.customer_coupon_id)
    summary = order_summary_service.summarize_cart(db, payload.cart, customer_coupon_id=payload.customer_coupon_id,
        account_id=account_id, modality=payload.modality, now=now)
    return CouponSelectionResponse(coupon=_read(coupon, discount, now), summary=summary,
                                   **summary.coupon.model_dump())

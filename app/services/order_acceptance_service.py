"""Revalidación comercial local de HU-025, sin reescribir el pedido ni reservar beneficios."""

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.models import Order
from app.schemas.cart import CartValidationInput
from app.schemas.order_summary import OrderSummaryResponse
from app.services import cart_service, menu_service, order_summary_service, order_tracking_service
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY


def _commercial_values(summary: OrderSummaryResponse):
    """Compara valores e identidades aplicadas; un cambio de nombre no modifica el importe pactado."""
    return {
        "subtotal": summary.subtotal,
        "discount_total": summary.discount_total,
        "total": summary.total,
        "items": [{
            "quantity": row.quantity,
            "subtotal": row.subtotal,
            "selection": row.selection.model_dump(exclude={"product_name", "variant_name", "complements"}) | {
                "complements": [option.model_dump(exclude={"name"}) for option in row.selection.complements],
            },
        } for row in summary.items],
        "applied_discounts": [row.model_dump(exclude={"name"}) for row in summary.applied_discounts],
        "coupon": summary.coupon.model_dump() if summary.coupon is not None else None,
    }


def validate_acceptance(db: Session, order: Order, *, clock=None) -> None:
    """Recalcula la selección original bajo el bloqueo compartido con sincronización y disponibilidad.

    El llamador mantiene bloqueado el pedido hasta confirmar. Las discrepancias
    conservan la solicitud pendiente; no se cambian importes, cupones ni recibos.
    """
    if not db.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
        raise AuthError(409, "Hay otra operación de catálogo en curso; consulta y reintenta.", code="CATALOG_BUSY")
    if not order.details:
        raise AuthError(409, "El pedido no tiene una selección verificable.", code="ORDER_SELECTION_CHANGED")
    try:
        historical = order_tracking_service.historical_summary(order)
        cart = CartValidationInput(items=[{
            "product_id": row.product_id, "product_variant_id": row.product_variant_id,
            "quantity": int(row.quantity),
            "complement_ids": [option.product_complement_id for option in row.complements],
        } for row in order.details])
    except ValidationError:
        raise AuthError(409, "La información histórica del pedido requiere revisión.", code="ORDER_HISTORY_INVALID") from None
    coupon = historical.coupon
    if coupon is not None and (not coupon.eligible or coupon.selected_coupon_id is None):
        raise AuthError(409, "El beneficio histórico requiere revisión.", code="ORDER_BENEFIT_CHANGED")
    now = clock() if clock is not None else datetime.now(timezone.utc)
    try:
        current = order_summary_service.summarize_cart(db, cart,
            customer_coupon_id=coupon.selected_coupon_id if coupon is not None else None,
            account_id=order.account_id, modality=order.modality, now=now)
    except cart_service.CartItemError:
        raise AuthError(409, "La selección o su disponibilidad cambiaron; revisa la solicitud.",
                        code="ORDER_SELECTION_CHANGED") from None
    except menu_service.MenuReadError:
        raise AuthError(503, "No fue posible comprobar el catálogo para confirmar el pedido.") from None
    except AuthError as error:
        if error.status_code in {401, 403, 404, 409, 422}:
            raise AuthError(409, "El beneficio seleccionado ya no puede confirmarse.", code="ORDER_BENEFIT_CHANGED") from None
        raise
    if current.coupon is not None and not current.coupon.eligible:
        raise AuthError(409, "El cupón ya no cumple sus condiciones; revisa la solicitud.", code="ORDER_BENEFIT_CHANGED")
    if _commercial_values(current) != _commercial_values(historical):
        raise AuthError(409, "Los precios o beneficios cambiaron; no se modificó el importe histórico.",
                        code="ORDER_QUOTE_CHANGED")

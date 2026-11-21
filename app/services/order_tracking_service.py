"""Lectura histórica y autorización privada por pedido, reutilizable en HU-023."""

from secrets import compare_digest
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.core.session_tokens import hash_session_token
from app.models.order import Order
from app.schemas.order_summary import OrderSummaryItem, OrderSummaryResponse
from app.schemas.orders import OrderTrackingResponse
from app.schemas.product_options import ProductSelectionResponse, SelectedComplement


class GuestOrderAccessError(LookupError):
    """No distingue entre pedido inexistente y acceso privado incorrecto."""


def historical_summary(order: Order) -> OrderSummaryResponse:
    """Mismo resumen persistido de HU-021, sin consultar precios del catálogo."""
    from app.services.commercial_discount_service import restore_snapshot
    summary = OrderSummaryResponse(
        subtotal=order.subtotal, discount_total=order.discount_total, total=order.total,
        items=[OrderSummaryItem(
            quantity=int(row.quantity), subtotal=row.subtotal,
            selection=ProductSelectionResponse(
                product_id=row.product_id, product_name=row.product_name,
                product_variant_id=row.product_variant_id, variant_name=row.variant_name,
                base_price=row.base_price, complements_total=row.complements_total, unit_total=row.unit_total,
                complements=[SelectedComplement(
                    product_modifier_id=option.product_modifier_id,
                    product_complement_id=option.product_complement_id,
                    name=option.name_snapshot, price=option.unit_price,
                ) for option in row.complements],
            ),
        ) for row in order.details],
    )
    return restore_snapshot(order, summary)


def authorize_guest_order(db, order_id: UUID, token: str | None) -> Order:
    """La clave autoriza sólo este pedido invitado; no concede identidad ni permisos."""
    try:
        hash_session_token(token)  # Reutiliza el formato canónico de 256 bits existente.
    except ValueError:
        raise GuestOrderAccessError() from None
    order = db.scalar(select(Order).where(Order.order_id == order_id, Order.account_id.is_(None)).options(
        load_only(Order.order_id, Order.guest_tracking_token, Order.status, Order.created_at,
                  Order.modality, Order.subtotal, Order.discount_total, Order.total, raiseload=True)))
    if order is None or order.guest_tracking_token is None or not compare_digest(order.guest_tracking_token, token):
        raise GuestOrderAccessError()
    return order


def track_guest_order(db, order_id: UUID, token: str | None) -> OrderTrackingResponse:
    order = authorize_guest_order(db, order_id, token)
    return OrderTrackingResponse(
        **historical_summary(order).model_dump(mode="python"),
        order_id=order.order_id, status=order.status, created_at=order.created_at, modality=order.modality,
    )

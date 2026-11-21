"""Cancela sólo solicitudes propias pendientes dentro de la transacción del llamador."""

from uuid import UUID

from sqlalchemy import select

from app.models.order import Order
from app.schemas.orders import OrderCancellationResponse
from app.services import order_state_service, order_tracking_service


class OrderNotPendingError(ValueError):
    """El pedido autorizado ya no admite cancelación directa."""


def cancel_order(db, order_id: UUID, *, account_id: int | None = None,
                 guest_token: str | None = None) -> OrderCancellationResponse:
    if account_id is None:
        order_tracking_service.authorize_guest_order(db, order_id, guest_token)
        owner = Order.account_id.is_(None)
    else:
        owner = Order.account_id == account_id
        if db.scalar(select(Order.order_id).where(Order.order_id == order_id, owner)) is None:
            # Mismo rechazo privado para pedido inexistente y pedido de otra cuenta.
            raise order_tracking_service.GuestOrderAccessError()

    # PostgreSQL bloquea la fila y vuelve a comprobar el estado si esperó otra
    # transacción. No se toma una decisión usando el estado leído al autorizar.
    identifier = order_state_service.transition_pending(db,order_id,"Cancelado",owner=owner,actor_account_id=account_id)
    if identifier is None:
        raise OrderNotPendingError()
    return OrderCancellationResponse(order_id=identifier)

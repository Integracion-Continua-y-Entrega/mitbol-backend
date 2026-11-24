"""Historial de la cuenta autenticada, sin privilegios de consulta por rol."""

from sqlalchemy import func, select
from sqlalchemy.orm import load_only, selectinload

from app.core.auth_errors import AuthError
from app.models import Order, OrderDetail
from app.models.order import INITIAL_STATUS
from app.schemas.customer_orders import CustomerOrderDetail, CustomerOrderListItem, CustomerOrderListResponse
from app.services.order_tracking_service import historical_summary


def list_orders(db, account_id, *, status=None, limit=25, offset=0):
    filters = [Order.account_id == account_id]
    if status is not None:
        filters.append(Order.status == status)
    total = db.scalar(select(func.count(Order.order_id)).where(*filters))
    rows = db.execute(select(Order.order_id, Order.status, Order.created_at, Order.modality,
                             Order.subtotal, Order.discount_total, Order.total)
                      .where(*filters).order_by(Order.created_at.desc(), Order.order_id.desc())
                      .limit(limit).offset(offset))
    return CustomerOrderListResponse(total=total, limit=limit, offset=offset, items=[
        CustomerOrderListItem(**row._mapping, can_cancel=row.status == INITIAL_STATUS) for row in rows
    ])


def order_detail(db, account_id, order_id):
    order = db.scalar(select(Order).where(Order.order_id == order_id, Order.account_id == account_id).options(
        load_only(Order.order_id, Order.status, Order.created_at, Order.modality, Order.subtotal,
                  Order.discount_total, Order.total, raiseload=True),
        selectinload(Order.details).selectinload(OrderDetail.complements),
    ))
    if order is None:
        # Mismo resultado para UUID inexistente, ajeno o de invitado.
        raise AuthError(404, "Pedido no encontrado.", code="ORDER_NOT_FOUND")
    return CustomerOrderDetail(
        **historical_summary(order).model_dump(mode="python"),
        order_id=order.order_id, status=order.status, created_at=order.created_at, modality=order.modality,
        can_cancel=order.status == INITIAL_STATUS,
    )

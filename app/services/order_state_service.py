"""Actualizaciones condicionales e historial en la misma transacción."""

from sqlalchemy import text, update

from app.core.auth_errors import AuthError
from app.models import Order, OrderStatusHistory
from app.models.order import INITIAL_STATUS


def history_available(db):
    return db.scalar(text("""SELECT to_regclass('public."OrderStatusHistories"') IS NOT NULL"""))


def record_state(db, order_id, status, *, previous_status=None, actor_account_id=None, reason=None, required=False):
    if not history_available(db):
        if required:
            raise AuthError(503, "La gestión de pedidos requiere la migración 0014.")
        return
    db.add(OrderStatusHistory(order_id=order_id,previous_status=previous_status,status=status,
                             actor_account_id=actor_account_id,reason=reason))
    db.flush()


def transition_pending(db, order_id, status, *, owner=None, actor_account_id=None, reason=None, required=False):
    conditions = [Order.order_id == order_id,Order.status == INITIAL_STATUS]
    if owner is not None:
        conditions.append(owner)
    identifier = db.scalar(update(Order).where(*conditions).values(status=status).returning(Order.order_id)
                           .execution_options(synchronize_session="fetch"))
    if identifier is not None:
        record_state(db,identifier,status,previous_status=INITIAL_STATUS,actor_account_id=actor_account_id,
                     reason=reason,required=required)
    return identifier

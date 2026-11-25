"""Verificación manual de HU-031, sin cambiar la atención ni registrar ventas externas."""

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.models import Order, OrderPayment
from app.models.order import CONFIRMED_STATUS, LEGACY_CONFIRMED_STATUS
from app.schemas.order_payments import OrderPaymentInput, OrderPaymentRead, OrderPaymentResponse


PAYABLE_STATUSES = frozenset({CONFIRMED_STATUS, LEGACY_CONFIRMED_STATUS, "En preparación",
                             "Listo para recoger", "Servido", "Entregado", "Completado"})


def _require_payments(db):
    if not db.scalar(text("""SELECT to_regclass('public."OrderPayments"') IS NOT NULL""")):
        raise AuthError(503, "La verificación de pagos requiere la migración 0015.", code="PAYMENTS_UNAVAILABLE")


def _order(db, order_id, *, lock=False):
    query = select(Order.order_id, Order.status, Order.total).where(Order.order_id == order_id)
    row = db.execute(query.with_for_update() if lock else query).one_or_none()
    if row is None:
        raise AuthError(404, "Pedido no encontrado.")
    return row


def _response(order, payment):
    return OrderPaymentResponse(order_id=order.order_id, total=order.total,
        payment_status="verified" if payment is not None else ("not_required" if order.total == 0 else "unverified"),
        payment=OrderPaymentRead.model_validate(payment) if payment is not None else None)


def get_payment(db: Session, order_id) -> OrderPaymentResponse:
    """Consulta el cobro histórico aunque cambie el estado de atención; leer no crea pagos."""
    _require_payments(db)
    order = _order(db, order_id)
    payment = db.scalar(select(OrderPayment).where(OrderPayment.order_id == order_id))
    return _response(order, payment)


def confirm_payment(db: Session, order_id, payload: OrderPaymentInput, *, actor_account_id: int) -> OrderPaymentResponse:
    """Serializa contra otros cobros y transiciones usando la fila del pedido hasta el commit del llamador."""
    try:
        payload = OrderPaymentInput.model_validate(payload)
    except ValidationError:
        raise AuthError(422, "Importe o método de pago inválidos.") from None
    _require_payments(db)
    order = _order(db, order_id, lock=True)
    if db.scalar(select(OrderPayment.payment_id).where(OrderPayment.order_id == order_id)) is not None:
        raise AuthError(409, "El pago ya fue verificado; consulta su registro.", code="PAYMENT_ALREADY_VERIFIED")
    if order.status not in PAYABLE_STATUSES:
        raise AuthError(409, "El estado del pedido no permite confirmar un pago.", code="PAYMENT_INVALID_STATE")
    if order.total == 0:
        raise AuthError(409, "El pedido no requiere cobro.", code="PAYMENT_NOT_REQUIRED")
    if payload.amount != order.total:
        raise AuthError(409, "El importe debe coincidir con el total histórico del pedido.", code="PAYMENT_AMOUNT_MISMATCH")
    payment = OrderPayment(order_id=order_id, amount=payload.amount, method=payload.method,
                           verified_by_account_id=actor_account_id)
    db.add(payment)
    db.flush()
    return _response(order, payment)

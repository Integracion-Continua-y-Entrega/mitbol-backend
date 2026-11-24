"""Consulta y decisión del personal; conserva precios y datos históricos."""

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from app.core.auth_errors import AuthError
from app.models import Order, OrderDetail, OrderStatusHistory
from app.models.order import CONFIRMED_STATUS, INITIAL_STATUS, LEGACY_CONFIRMED_STATUS
from app.schemas.orders import OrderRejectionInput, OrderStateRead, StaffOrderDetail, StaffOrderListItem, StaffOrderListResponse
from app.services import order_acceptance_service, order_state_service, order_tracking_service


def _require_history(db):
    if not order_state_service.history_available(db):
        raise AuthError(503,"La gestión de pedidos requiere la migración 0014.")


def _data(order):
    data = {"modality":order.modality,"name":order.requester_name}
    if order.modality == "pickup":
        data["phone"] = order.requester_phone
    return data


def _attention(order, now, pending_warning_seconds):
    """Calcula espera solo para solicitudes pendientes, sin persistir ni cambiar su estado."""
    if order.status != INITIAL_STATUS:
        return {"pending_wait_seconds": None, "needs_attention": False}
    seconds = max(0, int((now - order.created_at).total_seconds()))
    return {"pending_wait_seconds": seconds, "needs_attention": seconds >= pending_warning_seconds}


def list_orders(db, *, status=None, limit=25, offset=0, now=None, pending_warning_seconds=600):
    """Prioriza pendientes antes de paginar; cada grupo conserva primero las solicitudes más antiguas."""
    _require_history(db)
    now = now or datetime.now(timezone.utc)
    filters = [Order.status == status] if status is not None else []
    total = db.scalar(select(func.count(Order.order_id)).where(*filters))
    priority = case((Order.status == INITIAL_STATUS, 0), else_=1)
    rows = db.scalars(select(Order).where(*filters).order_by(priority, Order.created_at, Order.order_id)
                      .limit(limit).offset(offset))
    return StaffOrderListResponse(total=total,limit=limit,offset=offset,items=[StaffOrderListItem(
        order_id=row.order_id,status=row.status,created_at=row.created_at,data=_data(row),
        subtotal=row.subtotal,discount_total=row.discount_total,total=row.total,
        **_attention(row, now, pending_warning_seconds)) for row in rows])


def order_detail(db, order_id, *, now=None, pending_warning_seconds=600):
    """Devuelve precios e historial originales junto a la espera actual de una solicitud pendiente."""
    _require_history(db)
    now = now or datetime.now(timezone.utc)
    row = db.scalar(select(Order).where(Order.order_id == order_id).options(
        selectinload(Order.details).selectinload(OrderDetail.complements)))
    if row is None:
        raise AuthError(404,"Pedido no encontrado.")
    history = db.scalars(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)
                         .order_by(OrderStatusHistory.created_at,OrderStatusHistory.status_history_id))
    return StaffOrderDetail(**order_tracking_service.historical_summary(row).model_dump(),order_id=row.order_id,
        status=row.status,created_at=row.created_at,data=_data(row),
        history=[OrderStateRead.model_validate(event,from_attributes=True) for event in history],
        **_attention(row, now, pending_warning_seconds))


def decide_order(db, order_id, *, accepted, actor_account_id, reason=None, clock=None):
    """Bloquea el pedido vigente; aceptar revalida la cotización y rechazar exige motivo."""
    if not accepted:
        try:
            reason = OrderRejectionInput(reason=reason).reason
        except ValidationError:
            raise AuthError(422, "El rechazo requiere un motivo válido de 1 a 500 caracteres.") from None
    _require_history(db)
    query = select(Order).where(Order.order_id == order_id).with_for_update().execution_options(populate_existing=True)
    if accepted:
        query = query.options(selectinload(Order.details).selectinload(OrderDetail.complements))
    row = db.scalar(query)
    if row is None:
        raise AuthError(404,"Pedido no encontrado.")
    if row.status != INITIAL_STATUS:
        raise AuthError(409, "El pedido ya no está pendiente de aceptación.")
    if accepted:
        order_acceptance_service.validate_acceptance(db, row, clock=clock)
    identifier = order_state_service.transition_pending(db,order_id,CONFIRMED_STATUS if accepted else "Rechazado",
        actor_account_id=actor_account_id,reason=reason,required=True)
    if identifier is None:
        raise AuthError(409,"El pedido ya no está pendiente de aceptación.")
    return order_detail(db,identifier)


def _advance_order(db, order_id, *, previous_status, status, actor_account_id, modality=None):
    """Distingue pedidos inexistentes de conflictos sin aceptar estados enviados por el cliente."""
    _require_history(db)
    identifier = order_state_service.transition_state(db, order_id, previous_status=previous_status,
        status=status, actor_account_id=actor_account_id, modality=modality)
    if identifier is None:
        if db.scalar(select(Order.order_id).where(Order.order_id == order_id)) is None:
            raise AuthError(404, "Pedido no encontrado.")
        raise AuthError(409, "El estado o la modalidad del pedido no permiten esta acción.")
    return order_detail(db, identifier)


def start_preparation(db, order_id, *, actor_account_id):
    """Inicia desde Confirmado o Aceptado legado sin renombrar los eventos históricos."""
    _require_history(db)
    previous = db.scalar(select(Order.status).where(Order.order_id == order_id))
    if previous is None:
        raise AuthError(404, "Pedido no encontrado.")
    if previous not in {CONFIRMED_STATUS, LEGACY_CONFIRMED_STATUS}:
        raise AuthError(409, "El pedido no está confirmado para iniciar preparación.")
    return _advance_order(db, order_id, previous_status=previous, status="En preparación",
                          actor_account_id=actor_account_id)


def mark_ready_for_pickup(db, order_id, *, actor_account_id):
    """Marca listo un pedido pickup en preparación; no registra entrega, cobro ni canje."""
    return _advance_order(db, order_id, previous_status="En preparación", status="Listo para recoger",
                          actor_account_id=actor_account_id, modality="pickup")


def mark_served(db, order_id, *, actor_account_id):
    """Confirma que un pedido local en preparación fue servido, sin completar ni verificar cobros."""
    return _advance_order(db, order_id, previous_status="En preparación", status="Servido",
                          actor_account_id=actor_account_id, modality="dine_in")


def mark_delivered(db, order_id, *, actor_account_id):
    """Confirma la recogida efectiva de un pedido listo; no registra pago ni venta."""
    return _advance_order(db, order_id, previous_status="Listo para recoger", status="Entregado",
                          actor_account_id=actor_account_id, modality="pickup")


def complete_order(db, order_id, *, actor_account_id):
    """Finaliza la atención después de Servido o Entregado, según la modalidad.

    La modalidad consultada se revalida junto al estado en el UPDATE. Completar
    no confirma pagos, ventas, visitas ni canjes y requiere una acción explícita.
    """
    modality = db.scalar(select(Order.modality).where(Order.order_id == order_id))
    if modality is None:
        raise AuthError(404, "Pedido no encontrado.")
    previous_status = {"dine_in": "Servido", "pickup": "Entregado"}.get(modality)
    if previous_status is None:
        raise AuthError(409, "La modalidad del pedido no permite esta acción.")
    return _advance_order(db, order_id, previous_status=previous_status, status="Completado",
                          actor_account_id=actor_account_id, modality=modality)

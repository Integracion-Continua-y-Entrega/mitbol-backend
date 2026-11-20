"""Registro atómico e idempotente; el llamador controla la transacción."""

from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderDetail, OrderDetailComplement, RestaurantOrderSettings
from app.schemas.orders import OrderSubmissionInput, OrderSubmissionResponse
from app.schemas.order_summary import OrderSummaryItem
from app.schemas.product_options import ProductSelectionResponse, SelectedComplement
from app.services import order_summary_service


class OrderSubmissionError(ValueError):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _response(order: Order) -> OrderSubmissionResponse:
    """Reconstruye la confirmación sólo con copias históricas, incluso al reintentar."""
    contact = {"modality": order.modality, "name": order.requester_name}
    if order.modality == "pickup":
        contact["phone"] = order.requester_phone
    return OrderSubmissionResponse(
        order_id=order.order_id, status=order.status, created_at=order.created_at, data=contact,
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


def submit_order(db, payload: OrderSubmissionInput, idempotency_key: UUID, *, account_id=None) -> OrderSubmissionResponse:
    """Reutiliza HU-019 → HU-016 → HU-017; una sesión activa identifica la cuenta."""
    fingerprint = sha256(json.dumps(
        {"account_id": account_id, **payload.model_dump(mode="json")},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    # La misma clave se serializa entre procesos/conexiones hasta commit o rollback.
    # El índice UNIQUE es una segunda garantía; no hay registros intermedios de reserva.
    lock_key = int.from_bytes(idempotency_key.bytes[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key).options(
        selectinload(Order.details).selectinload(OrderDetail.complements)))
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise OrderSubmissionError(409, "La clave de idempotencia ya corresponde a otra solicitud.")
        return _response(existing)

    settings = db.scalar(select(RestaurantOrderSettings).where(
        RestaurantOrderSettings.settings_id == 1).with_for_update(read=True))
    if settings is None or not settings.accepting_orders:
        raise OrderSubmissionError(409, "El restaurante no está aceptando pedidos.")
    summary = order_summary_service.summarize_cart(db, payload.cart)
    order = Order(
        order_id=uuid4(), idempotency_key=idempotency_key, request_hash=fingerprint, account_id=account_id,
        modality=payload.data.modality, requester_name=payload.data.name,
        requester_phone=getattr(payload.data, "phone", None),
        subtotal=summary.subtotal, discount_total=summary.discount_total, total=summary.total,
        details=[OrderDetail(
            line_index=index, product_id=row.selection.product_id,
            product_variant_id=row.selection.product_variant_id, product_name=row.selection.product_name,
            variant_name=row.selection.variant_name, quantity=row.quantity, base_price=row.selection.base_price,
            complements_total=row.selection.complements_total, unit_total=row.selection.unit_total, subtotal=row.subtotal,
            complements=[OrderDetailComplement(
                position=position, product_complement_id=option.product_complement_id,
                product_modifier_id=option.product_modifier_id, name_snapshot=option.name, unit_price=option.price,
            ) for position, option in enumerate(row.selection.complements)],
        ) for index, row in enumerate(summary.items)],
    )
    db.add(order)
    db.flush()
    return _response(order)

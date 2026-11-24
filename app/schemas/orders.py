"""Contrato de envío: sólo selecciones y datos de atención, nunca importes."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, field_validator

from app.schemas.cart import CartValidationInput
from app.schemas.order_modality import OrderModalityInput
from app.schemas.order_summary import OrderSummaryResponse
from app.schemas.menu import MenuAmount


class OrderSubmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    cart: CartValidationInput
    data: OrderModalityInput
    customer_coupon_id: PositiveInt | None = None

    @field_validator("cart")
    @classmethod
    def nonempty_cart(cls, value):
        if not value.items:
            raise ValueError("La solicitud debe contener al menos un producto.")
        return value


class GuestTrackingAccess(BaseModel):
    path: str
    token: str = Field(repr=False, description="Acceso privado del pedido; enviar como Authorization: Bearer <token>.")
    token_type: Literal["Bearer"] = "Bearer"


class OrderTrackingResponse(OrderSummaryResponse):
    order_id: UUID
    status: str = Field(description="Estado actual almacenado; consultar de nuevo para obtener cambios.")
    created_at: datetime
    modality: Literal["dine_in", "pickup"]


class OrderSubmissionResponse(OrderSummaryResponse):
    order_id: UUID
    status: str = Field(description="Solicitud recibida al crear; estado vigente al recuperar un envío idempotente.")
    created_at: datetime
    data: OrderModalityInput
    tracking: GuestTrackingAccess | None = Field(default=None, repr=False)


class OrderCancellationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OrderCancellationResponse(BaseModel):
    order_id: UUID
    status: Literal["Cancelado"] = "Cancelado"


class OrderAcceptanceInput(OrderCancellationInput):
    pass


class OrderPreparationInput(OrderCancellationInput):
    """Acción sin parámetros: el servidor decide la transición y la modalidad permitidas."""


class OrderFulfillmentInput(OrderCancellationInput):
    """Confirmación explícita sin importes, método de pago ni estado enviados por el cliente."""


class OrderRejectionInput(OrderCancellationInput):
    reason: Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=500)] | None = None

    @field_validator("reason")
    @classmethod
    def visible_reason(cls, value):
        if value is not None and not value.isprintable():
            raise ValueError("El motivo debe contener texto visible.")
        return value


class OrderStateRead(BaseModel):
    status_history_id: UUID
    previous_status: str | None
    status: str
    actor_account_id: int | None
    reason: str | None
    created_at: datetime


class StaffOrderListItem(BaseModel):
    order_id: UUID
    status: str
    created_at: datetime
    data: OrderModalityInput
    subtotal: MenuAmount
    discount_total: MenuAmount
    total: MenuAmount
    pending_wait_seconds: int | None = Field(default=None, ge=0,
        description="Segundos de espera actuales; null cuando la solicitud ya fue atendida.")
    needs_attention: bool = Field(default=False, description="Solicitud pendiente que alcanzó el umbral de aviso.")


class StaffOrderListResponse(BaseModel):
    items: list[StaffOrderListItem]
    total: int
    limit: int
    offset: int


class StaffOrderDetail(OrderSummaryResponse):
    order_id: UUID
    status: str
    created_at: datetime
    data: OrderModalityInput
    history: list[OrderStateRead]
    pending_wait_seconds: int | None = Field(default=None, ge=0,
        description="Segundos de espera actuales; null cuando la solicitud ya fue atendida.")
    needs_attention: bool = Field(default=False, description="Solicitud pendiente que alcanzó el umbral de aviso.")

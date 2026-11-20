"""Contrato de envío: sólo selecciones y datos de atención, nunca importes."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.cart import CartValidationInput
from app.schemas.order_modality import OrderModalityInput
from app.schemas.order_summary import OrderSummaryResponse


class OrderSubmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    cart: CartValidationInput
    data: OrderModalityInput

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

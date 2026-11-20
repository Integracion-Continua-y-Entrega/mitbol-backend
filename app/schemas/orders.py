"""Contrato de envío: sólo selecciones y datos de atención, nunca importes."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

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


class OrderSubmissionResponse(OrderSummaryResponse):
    order_id: UUID
    status: Literal["Solicitud recibida"]
    created_at: datetime
    data: OrderModalityInput

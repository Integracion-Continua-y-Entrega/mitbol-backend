"""Confirmación manual: el cliente declara importe y método; el servidor acredita responsable y fecha."""

from datetime import datetime
from decimal import Decimal
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.menu import MenuAmount


PaymentMethod = Literal["cash", "card", "transfer", "other"]


class OrderPaymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    amount: Annotated[Decimal, Field(gt=0, allow_inf_nan=False,
        description="Importe exacto en MXN como cadena decimal, sin exponentes; debe igualar el total histórico.")]
    method: PaymentMethod

    @field_validator("amount", mode="before", json_schema_input_type=str)
    @classmethod
    def decimal_string(cls, value):
        """Evita convertir números JSON binarios a dinero o redondear el importe declarado."""
        if isinstance(value, Decimal):
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
            return Decimal(value)
        raise ValueError("El importe debe ser una cadena decimal positiva.")


class OrderPaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: UUID
    amount: MenuAmount
    method: PaymentMethod
    verified_at: datetime
    verified_by_account_id: int


class OrderPaymentResponse(BaseModel):
    order_id: UUID
    currency: Literal["MXN"] = "MXN"
    total: MenuAmount
    payment_status: Literal["unverified", "verified", "not_required"]
    payment: OrderPaymentRead | None

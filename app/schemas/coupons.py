"""Selección preliminar: un identificador, sin importes aceptados del navegador."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator

from app.schemas.auth import AuthInput
from app.schemas.cart import CartValidationInput
from app.schemas.menu import MenuAmount
from app.schemas.order_summary import OrderSummaryResponse


class CouponConditions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["order"] = Field(description="Alcance explícito sobre el subtotal completo del carrito.")
    minimum_subtotal: MenuAmount | None = None
    minimum_quantity: PositiveInt | None = None
    modalities: list[Literal["dine_in", "pickup"]] | None = Field(default=None, min_length=1)

    @field_validator("minimum_subtotal", mode="before")
    @classmethod
    def stored_amount(cls, value):
        # JSONB almacena el importe como cadena; no convertir floats binarios.
        if value is None or isinstance(value, Decimal):
            return value
        if isinstance(value, str) or type(value) is int:
            try:
                return Decimal(value)
            except InvalidOperation:
                pass
        raise ValueError("Condición monetaria inválida.")


class CouponRead(BaseModel):
    customer_coupon_id: int
    name: str
    description: str | None
    status: Literal["available", "reserved", "redeemed", "expired"]
    starts_at: datetime
    expires_at: datetime
    available: bool
    conditions: CouponConditions | None
    conditions_status: Literal["defined", "pending"]
    discount_type: str | None
    discount_value: MenuAmount | None


class CouponListResponse(BaseModel):
    coupons: list[CouponRead]


class CouponSelectionInput(AuthInput):
    customer_coupon_id: PositiveInt = Field(description="Un solo cupón; no admite listas ni importes.")
    cart: CartValidationInput
    modality: Literal["dine_in", "pickup"]


class CouponSelectionResponse(BaseModel):
    coupon: CouponRead
    selected_coupon_id: int | None
    eligible: bool
    reason: str | None
    calculation_status: Literal["calculated", "rules_pending", "not_eligible"]
    benefit_amount: MenuAmount | None
    preliminary: Literal[True] = True
    order_integration_status: Literal["not_integrated"] = "not_integrated"
    summary: OrderSummaryResponse = Field(description="Resumen HU-019 sin aplicar el cupón al total definitivo.")

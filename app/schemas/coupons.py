"""Selección preliminar: un identificador, sin importes aceptados del navegador."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

from app.schemas.auth import AuthInput
from app.schemas.cart import CartValidationInput
from app.schemas.menu import MenuAmount
from app.schemas.order_summary import CouponApplicationResult, OrderSummaryResponse


class CouponConditions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["order", "products"] = Field(description="order incluye el subtotal completo; products exige productos y alcance de complementos explícitos.")
    minimum_subtotal: MenuAmount | None = None
    minimum_quantity: PositiveInt | None = None
    modalities: list[Literal["dine_in", "pickup"]] | None = Field(default=None, min_length=1)
    product_ids: list[PositiveInt] | None = Field(default=None, min_length=1)
    include_complements: bool | None = None
    compatible_discount_ids: list[PositiveInt] | None = None

    @model_validator(mode="after")
    def explicit_product_scope(self):
        if self.scope == "products" and (not self.product_ids or self.include_complements is None):
            raise ValueError("El alcance por productos requiere productos y tratamiento de complementos.")
        if self.scope == "order" and self.product_ids is not None:
            raise ValueError("El alcance order no admite restricciones de productos contradictorias.")
        return self

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


class CouponSelectionResponse(CouponApplicationResult):
    coupon: CouponRead
    preliminary: Literal[True] = True
    order_integration_status: Literal["integrated"] = "integrated"
    summary: OrderSummaryResponse = Field(description="Resumen HU-019 con el beneficio aplicable, recalculado al enviar el pedido.")

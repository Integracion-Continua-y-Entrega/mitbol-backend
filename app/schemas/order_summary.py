"""Resumen calculado con precios del catálogo, sin registrar una solicitud."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.cart import ValidatedCartItem
from app.schemas.menu import MenuAmount


class OrderSummaryItem(ValidatedCartItem):
    subtotal: MenuAmount = Field(description="quantity × selection.unit_total; incluye complementos.")


class OrderSummaryResponse(BaseModel):
    currency: Literal["MXN"] = "MXN"
    items: list[OrderSummaryItem]
    subtotal: MenuAmount
    discount_total: MenuAmount = Decimal("0")
    total: MenuAmount = Field(description="Igual al subtotal mientras no se integren descuentos comerciales.")
    discounts_status: Literal["not_integrated"] = Field(
        default="not_integrated",
        description="El catálogo sincroniza descuentos, pero aún no determina su aplicación ni su desglose.",
    )

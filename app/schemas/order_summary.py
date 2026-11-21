"""Resumen calculado con precios del catálogo, sin registrar una solicitud."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.cart import ValidatedCartItem
from app.schemas.menu import MenuAmount
from app.schemas.commercial_discounts import AppliedCommercialDiscount, PendingCommercialDiscount


class OrderSummaryItem(ValidatedCartItem):
    subtotal: MenuAmount = Field(description="quantity × selection.unit_total; incluye complementos.")


class OrderSummaryResponse(BaseModel):
    currency: Literal["MXN"] = "MXN"
    items: list[OrderSummaryItem]
    subtotal: MenuAmount
    discount_total: MenuAmount = Decimal("0")
    total: MenuAmount = Field(description="Subtotal menos descuentos comerciales calculados; nunca incluye el cupón preliminar.")
    applied_discounts: list[AppliedCommercialDiscount] = Field(default_factory=list)
    pending_discounts: list[PendingCommercialDiscount] = Field(default_factory=list)
    discounts_status: Literal["not_integrated", "not_applicable", "applied", "rules_pending"] = Field(
        default="not_integrated",
        description="rules_pending indica condiciones comerciales incompletas; los descuentos pendientes no se aplican.",
    )

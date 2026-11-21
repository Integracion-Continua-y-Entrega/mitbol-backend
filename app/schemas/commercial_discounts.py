"""Condiciones de confianza almacenadas en PostgreSQL; nunca recibidas de React."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.schemas.menu import MenuAmount


class CommercialDiscountConditions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    include_complements: bool
    compatible_discount_ids: list[PositiveInt]
    # Ausentes: reglas internas del MVP (saldo restante, fijo una vez por
    # carrito y prorrateo en centavos). Valores distintos no se aplican.
    percentage_base: Literal["original", "remaining"] | None = None
    fixed_scope: Literal["eligible_subtotal", "unit"] | None = None
    fixed_allocation: Literal["proportional"] | None = None
    application_order: int | None = Field(default=None, ge=0)


class DiscountAllocation(BaseModel):
    item_index: int
    base: MenuAmount
    amount: MenuAmount


class AppliedCommercialDiscount(BaseModel):
    discount_id: int
    name: str
    discount_type: str
    value: MenuAmount
    base: MenuAmount
    amount: MenuAmount
    allocations: list[DiscountAllocation]


class PendingCommercialDiscount(BaseModel):
    discount_id: int
    name: str
    reason: str

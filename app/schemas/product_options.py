"""Selección de una unidad de producto; no representa un carrito ni un pedido."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator

from app.schemas.menu import MenuAmount


class ProductSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_variant_id: PositiveInt
    complement_ids: list[PositiveInt] = Field(
        default_factory=list, description="IDs de opciones seleccionadas, sin repetidos. Repite este parámetro por opción."
    )

    @field_validator("complement_ids")
    @classmethod
    def unique_complements(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Los complementos seleccionados no deben repetirse.")
        return value


class SelectedComplement(BaseModel):
    product_modifier_id: int
    product_complement_id: int
    name: str
    price: MenuAmount


class ProductSelectionResponse(BaseModel):
    currency: Literal["MXN"] = "MXN"
    product_id: int
    product_name: str
    product_variant_id: int
    variant_name: str
    base_price: MenuAmount
    complements: list[SelectedComplement]
    complements_total: MenuAmount
    unit_total: MenuAmount

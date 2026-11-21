"""Contratos del carrito del navegador, sin persistencia ni totales de pedido."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from app.schemas.product_options import ProductSelectionInput, ProductSelectionResponse


class CartItemInput(ProductSelectionInput):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: PositiveInt
    quantity: PositiveInt


class CartValidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[CartItemInput] = Field(description="Líneas del carrito en su orden original; puede estar vacío.")


class CartSummaryInput(CartValidationInput):
    customer_coupon_id: PositiveInt | None = None
    modality: Literal["dine_in", "pickup"] | None = None

    @model_validator(mode="after")
    def coupon_requires_modality(self):
        if self.customer_coupon_id is not None and self.modality is None:
            raise ValueError("Selecciona la modalidad para validar el cupón.")
        return self


class ValidatedCartItem(BaseModel):
    quantity: PositiveInt
    selection: ProductSelectionResponse


class CartValidationResponse(BaseModel):
    valid: Literal[True] = True
    items: list[ValidatedCartItem]

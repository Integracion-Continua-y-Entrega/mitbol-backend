"""Contratos del carrito del navegador, sin persistencia ni totales de pedido."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.schemas.product_options import ProductSelectionInput, ProductSelectionResponse


class CartItemInput(ProductSelectionInput):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: PositiveInt
    quantity: PositiveInt


class CartValidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[CartItemInput] = Field(description="Líneas del carrito en su orden original; puede estar vacío.")


class ValidatedCartItem(BaseModel):
    quantity: PositiveInt
    selection: ProductSelectionResponse


class CartValidationResponse(BaseModel):
    valid: Literal[True] = True
    items: list[ValidatedCartItem]

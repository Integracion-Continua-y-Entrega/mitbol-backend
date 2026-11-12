"""Tipos de descuento del proveedor; reconocerlos no implica aplicarlos a ventas."""

from enum import Enum


class DiscountType(str, Enum):
    """Identificadores originales del proveedor, sin traducciones comerciales."""

    FIXED_PERCENT = "FIXED_PERCENT"
    FIXED_AMOUNT = "FIXED_AMOUNT"
    VARIABLE_PERCENT = "VARIABLE_PERCENT"
    VARIABLE_AMOUNT = "VARIABLE_AMOUNT"
    DISCOUNT_BY_POINTS = "DISCOUNT_BY_POINTS"


def is_variable_discount(discount_type: DiscountType | str) -> bool:
    """Indica si el tipo requiere definir porcentaje/importe durante la venta.

    Un identificador desconocido debe fallar antes de importar datos.
    False no significa necesariamente descuento fijo: existe el tipo por puntos.
    """
    kind = DiscountType(discount_type)

    return kind in (DiscountType.VARIABLE_PERCENT, DiscountType.VARIABLE_AMOUNT)

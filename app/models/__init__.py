"""Registro exclusivo de las cinco tablas del catálogo de HU-008."""

from app.db.base import Base
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.product_modifier import ProductModifier
from app.models.product_complement import ProductComplement
from app.models.product_modifier_assignment import ProductModifierAssignment

__all__ = [
    "Base", "Product", "ProductVariant", "ProductModifier",
    "ProductComplement", "ProductModifierAssignment",
]

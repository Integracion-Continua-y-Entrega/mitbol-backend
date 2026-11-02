"""Registro del catálogo HU-008 y del enriquecimiento local HU-005."""

from app.db.base import Base
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.product_modifier import ProductModifier
from app.models.product_complement import ProductComplement
from app.models.product_modifier_assignment import ProductModifierAssignment
from app.models.product_image import ProductImage
from app.models.tag import Tag
from app.models.product_tag import ProductTag

__all__ = [
    "Base", "Product", "ProductVariant", "ProductModifier",
    "ProductComplement", "ProductModifierAssignment",
    "ProductImage", "Tag", "ProductTag",
]

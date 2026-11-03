"""Reglas puras compartidas por el menú público y la administración local."""

from app.models import Product, ProductVariant


def variant_can_order(product: Product, variant: ProductVariant) -> bool:
    return bool(
        product.is_active and product.is_available
        and variant.is_available and variant.is_commercially_available
    )


def product_can_order(product: Product) -> bool:
    return any(variant_can_order(product, variant) for variant in product.variants)

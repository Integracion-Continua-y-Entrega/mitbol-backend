"""Validación y precio unitario con el catálogo vigente, sin persistir selecciones."""

from decimal import Decimal, localcontext

from app.schemas.product_options import ProductSelectionInput, ProductSelectionResponse, SelectedComplement
from app.services import menu_service


class InvalidSelectionError(ValueError):
    """La variante u opciones no corresponden al producto seleccionado."""


class UnavailableSelectionError(InvalidSelectionError):
    """La selección existe pero no puede pedirse actualmente."""


def validate_product_selection(db, product_id: int, selection: ProductSelectionInput) -> ProductSelectionResponse:
    """Reutilizable por carrito/resumen/pedido; conserva Decimal y no hace commit."""
    product = menu_service.get_product_detail(db, product_id).product
    variant = next((item for item in product.variants
                    if item.product_variant_id == selection.product_variant_id), None)
    if variant is None:
        raise InvalidSelectionError("La variante no existe o no pertenece al producto.")
    if not variant.can_order:
        raise UnavailableSelectionError("El producto o la variante no están disponibles.")

    allowed = {
        option.product_complement_id: (modifier.product_modifier_id, option)
        for modifier in variant.modifiers for option in modifier.complements
    }
    complements = []
    for identifier in selection.complement_ids:
        if identifier not in allowed:
            raise InvalidSelectionError("El complemento no existe o no está permitido para la variante.")
        modifier_id, option = allowed[identifier]
        if not option.can_order:
            raise UnavailableSelectionError("El complemento seleccionado no está disponible.")
        complements.append(SelectedComplement(
            product_modifier_id=modifier_id, product_complement_id=identifier,
            name=option.name, price=option.price,
        ))

    prices = [variant.price, *(option.price for option in complements)]
    # Numeric no tiene escala fija: ampliar la precisión evita redondear al sumar.
    scale = max(0, -min(price.as_tuple().exponent for price in prices))
    precision = max(price.adjusted() for price in prices) + scale + len(str(len(prices))) + 2
    with localcontext() as context:
        context.prec = max(context.prec, precision)
        complements_total = sum((option.price for option in complements), Decimal("0"))
        unit_total = variant.price + complements_total

    return ProductSelectionResponse(
        product_id=product.product_id, product_name=product.name,
        product_variant_id=variant.product_variant_id, variant_name=variant.name,
        base_price=variant.price, complements=complements,
        complements_total=complements_total, unit_total=unit_total,
    )

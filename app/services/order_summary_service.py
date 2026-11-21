"""Importes exactos del carrito vigente, reutilizables al registrar un pedido."""

from decimal import Decimal, localcontext

from app.schemas.cart import CartValidationInput
from app.schemas.order_summary import OrderSummaryItem, OrderSummaryResponse
from app.services import cart_service, commercial_discount_service


def summarize_cart(db, cart: CartValidationInput, *, customer_coupon_id=None, account_id=None,
                   modality=None, now=None) -> OrderSummaryResponse:
    """Revalida HU-016/HU-017 en cada consulta; no persiste ni reserva precios."""
    validated = cart_service.validate_cart(db, cart)
    # Numeric no tiene escala fija. Incluye los dígitos de las cantidades y el
    # posible acarreo de la suma para no redondear siquiera con importes grandes.
    scale = max((max(0, -row.selection.unit_total.as_tuple().exponent) for row in validated.items), default=0)
    precision = max((row.selection.unit_total.adjusted() + len(str(row.quantity))
                     for row in validated.items), default=0) + scale + len(str(len(validated.items))) + 2
    with localcontext() as context:
        context.prec = max(context.prec, precision)
        items = [OrderSummaryItem(quantity=row.quantity, selection=row.selection,
                                  subtotal=row.selection.unit_total * row.quantity)
                 for row in validated.items]
        subtotal = sum((row.subtotal for row in items), Decimal("0"))

    summary = OrderSummaryResponse(items=items, subtotal=subtotal, total=subtotal)
    if customer_coupon_id is not None:
        from app.services.coupon_service import apply_coupon
        return apply_coupon(db, summary, customer_coupon_id, account_id=account_id, modality=modality, now=now)
    return commercial_discount_service.apply_discounts(db, summary)

"""Valida líneas mediante HU-017; no guarda, reserva ni confirma pedidos."""

from app.schemas.cart import CartValidationInput, CartValidationResponse, ValidatedCartItem
from app.services import menu_service, product_options_service


class CartItemError(ValueError):
    def __init__(self, item_index: int, status_code: int, message: str):
        super().__init__(message)
        self.item_index = item_index
        self.status_code = status_code


def validate_cart(db, cart: CartValidationInput) -> CartValidationResponse:
    """Devuelve selecciones con Decimal para reutilizarlas en resumen y pedido."""
    items = []
    for index, item in enumerate(cart.items):
        try:
            selection = product_options_service.validate_product_selection(db, item.product_id, item)
        except menu_service.MenuProductNotFoundError:
            raise CartItemError(index, 404, "Producto no encontrado.") from None
        except product_options_service.UnavailableSelectionError as error:
            raise CartItemError(index, 409, str(error)) from None
        except product_options_service.InvalidSelectionError as error:
            raise CartItemError(index, 422, str(error)) from None
        items.append(ValidatedCartItem(quantity=item.quantity, selection=selection))
    return CartValidationResponse(items=items)

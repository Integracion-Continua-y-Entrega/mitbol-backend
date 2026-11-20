"""Validación y resumen públicos del carrito; el estado se conserva en el frontend."""

from contextlib import contextmanager
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.db.database import get_db
from app.schemas.cart import CartValidationInput, CartValidationResponse
from app.schemas.order_summary import OrderSummaryResponse
from app.services import cart_service, menu_service, order_summary_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cart", tags=["Carrito"])


@contextmanager
def _handle_cart_errors():
    try:
        yield
    except AuthError:
        raise
    except cart_service.CartItemError as error:
        raise HTTPException(error.status_code, {"item_index": error.item_index, "message": str(error)},
                            headers={"Cache-Control": "no-store"}) from None
    except menu_service.MenuUnavailableError:
        raise HTTPException(503, "El carrito no puede validarse temporalmente.",
                            headers={"Cache-Control": "no-store"}) from None
    except Exception as error:
        cause = error.__cause__ if isinstance(error, menu_service.MenuReadError) else error
        logger.error("Falló la validación del carrito (%s).", type(cause).__name__)
        raise HTTPException(500, "No fue posible validar el carrito.",
                            headers={"Cache-Control": "no-store"}) from None


@router.post(
    "/validate", response_model=CartValidationResponse,
    summary="Validar el contenido de un carrito",
    description=(
        "Validación pública sin escrituras, disponible también para invitados. "
        "Recibe IDs y cantidades enteras positivas; rechaza precios o importes enviados por el cliente. "
        "Reutiliza HU-017 y devuelve cada cantidad con su selección y precio unitario vigente. "
        "Un carrito vacío es válido. No calcula el resumen definitivo ni registra un pedido. "
        "Si una línea falla, no devuelve un carrito parcialmente validado. "
        "Los errores de catálogo usan detail.item_index (desde cero) y detail.message; "
        "los errores de contrato identifican el campo en detail[].loc."
    ),
    responses={
        404: {"description": "Un producto no existe o está inactivo."},
        409: {"description": "Una selección no está disponible."},
        422: {"description": "Contrato, cantidad o configuración inválidos."},
        500: {"description": "Error interno al consultar el catálogo."},
        503: {"description": "Catálogo temporalmente no disponible."},
    },
)
def validate_cart(payload: CartValidationInput, response: Response, db: Session = Depends(get_db)) -> CartValidationResponse:
    response.headers["Cache-Control"] = "no-store"
    with _handle_cart_errors():
        return cart_service.validate_cart(db, payload)


@router.post(
    "/summary", response_model=OrderSummaryResponse,
    summary="Consultar resumen e importes del carrito",
    description=(
        "Disponible para invitados y clientes registrados, sin escrituras. Recibe el mismo contrato que /validate: "
        "items con product_id, product_variant_id, quantity y complement_ids opcionales. "
        "Revalida disponibilidad y opciones con HU-016/HU-017; rechaza precios o totales del cliente. "
        "Cada selection incluye nombres, precio base, complementos con sus precios y unit_total (precio por unidad). "
        "El subtotal de línea es quantity × selection.unit_total. Devuelve la suma y el total como cadenas decimales exactas. "
        "Los descuentos comerciales aún no están integrados: discounts_status=not_integrated, discount_total=0 y total=subtotal. "
        "Un carrito vacío devuelve importes cero. No registra pedidos ni garantiza precios para una solicitud posterior. "
        "Los errores de catálogo identifican detail.item_index (desde cero) y detail.message."
    ),
    responses={
        404: {"description": "Un producto no existe o está inactivo."},
        409: {"description": "Una selección no está disponible."},
        422: {"description": "Contrato, cantidad o configuración inválidos."},
        500: {"description": "Error interno al calcular el resumen."},
        503: {"description": "Catálogo temporalmente no disponible."},
    },
)
def summarize_cart(payload: CartValidationInput, response: Response, db: Session = Depends(get_db)) -> OrderSummaryResponse:
    response.headers["Cache-Control"] = "no-store"
    with _handle_cart_errors():
        return order_summary_service.summarize_cart(db, payload)

"""Validación pública del carrito; el estado se conserva en el frontend."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.cart import CartValidationInput, CartValidationResponse
from app.services import cart_service, menu_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cart", tags=["Carrito"])


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
    try:
        return cart_service.validate_cart(db, payload)
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

"""Endpoint público de consulta del menú digital."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.menu import MenuErrorResponse, MenuResponse
from app.services import menu_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Menú"])


@router.get(
    "/menu",
    response_model=MenuResponse,
    summary="Consultar el menú digital",
    description=(
        "Consulta pública sin autenticación, basada en PostgreSQL y sin llamadas a Loyverse. "
        "Excluye productos comercialmente inactivos y mantiene visibles los agotados. "
        "Utiliza can_order para habilitar la selección: is_available conserva el estado almacenado. "
        "Las variantes no tienen is_active y su estado no permite distinguir agotamiento de baja. "
        "Los importes son cadenas decimales exactas, con al menos dos decimales. "
        "El rango usa variantes disponibles; si ninguna está disponible, sus precios son de referencia. "
        "Sin variantes, los precios son null y can_order es false. "
        "image_url es siempre null. Un catálogo vacío devuelve 200 con products: []. "
        "Los ejemplos ilustran el contrato; los datos devueltos proceden de la base."
    ),
    responses={
        500: {
            "model": MenuErrorResponse,
            "description": "Error interno de consulta o integridad del catálogo.",
            "content": {"application/json": {"example": {
                "detail": "No fue posible consultar el menú.",
            }}},
        },
        503: {
            "model": MenuErrorResponse,
            "description": "Conexión a PostgreSQL no disponible o tiempo de espera agotado.",
            "content": {"application/json": {"example": {
                "detail": "El menú no está disponible temporalmente.",
            }}},
        },
    },
)
def get_menu(db: Session = Depends(get_db)) -> MenuResponse:
    try:
        return menu_service.get_menu(db)
    except menu_service.MenuUnavailableError as error:
        # Sólo se registra el tipo: el mensaje del controlador puede contener SQL o credenciales.
        logger.warning("Menú temporalmente no disponible (%s).", type(error.__cause__).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El menú no está disponible temporalmente.",
        ) from None
    except Exception as error:
        cause = error.__cause__ if isinstance(error, menu_service.MenuReadError) else error
        logger.error("Falló la consulta del menú (%s).", type(cause).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No fue posible consultar el menú.",
        ) from None

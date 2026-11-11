"""Endpoint público de consulta del menú digital."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import PositiveInt
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.menu import MenuErrorResponse, MenuProductDetailResponse, MenuPublicConfigResponse, MenuResponse, MenuTag
from app.services import menu_service, menu_tags

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["Menú"])


@router.get(
    "/menu",
    response_model=MenuResponse,
    summary="Consultar el menú digital",
    description=(
        "Consulta pública sin autenticación, basada en PostgreSQL y sin llamadas a Loyverse. "
        "Permite buscar por fragmento del nombre mediante q, sin distinguir mayúsculas "
        "e ignorando espacios exteriores; omitirlo o dejarlo vacío devuelve el listado general. "
        "Permite repetir tag_id para filtrar por cualquiera de las etiquetas seleccionadas. "
        "Si se proporciona q, se aplican ambos filtros; se conservan todas las variantes. "
        "Excluye productos comercialmente inactivos y mantiene visibles los agotados. "
        "Utiliza can_order para habilitar la selección: is_available conserva el estado almacenado. "
        "can_order incluye la disponibilidad comercial de la variante sincronizada desde Loyverse. "
        "Los importes son cadenas decimales exactas, con al menos dos decimales. "
        "El rango usa variantes con can_order=true; si ninguna puede pedirse, sus precios son de referencia. "
        "Sin variantes, los precios son null y can_order es false. "
        "image_url representa la primera fotografía local por display_order, variante e ID; null si no hay. "
        "Cada variante incluye image_url y tags; el detalle añade su galería images. "
        "Un catálogo vacío devuelve 200 con products: []. "
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
def get_menu(
    db: Session = Depends(get_db),
    q: Annotated[
        str | None,
        Query(
            description=(
                "Fragmento del nombre del producto, sin distinguir mayúsculas y con "
                "espacios exteriores ignorados. Vacío o ausente: sin filtro por nombre."
            ),
        ),
    ] = None,
    tag_ids: Annotated[
        list[PositiveInt] | None,
        Query(
            alias="tag_id",
            description=(
                "IDs positivos de etiquetas. Repite tag_id para seleccionar varias; "
                "basta con cualquiera de ellas. Se combina con q mediante AND."
            ),
        ),
    ] = None,
) -> MenuResponse:
    try:
        return menu_service.get_menu(db, q=q, tag_ids=tag_ids)
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


@router.get(
    "/menu/config",
    response_model=MenuPublicConfigResponse,
    summary="Consultar la URL pública del menú para el QR general",
    description=(
        "HU-004: URL del frontend definida por PUBLIC_MENU_URL, independiente del catálogo. "
        "Consulta pública sin sesión, acceso a PostgreSQL ni llamadas a Loyverse. "
        "Devuelve menu_url: null si aún no está configurada. "
        "Frontend genera la vista previa, imagen descargable e impresión del QR. "
        "No se deriva la URL de Host ni de cabeceras de proxy."
    ),
)
def get_public_menu_config(request: Request, response: Response) -> MenuPublicConfigResponse:
    response.headers["Cache-Control"] = "no-store"
    return MenuPublicConfigResponse(menu_url=request.app.state.settings.public_menu_url)


@router.get(
    "/menu/tags",
    response_model=list[MenuTag],
    summary="Consultar etiquetas de productos",
    description=(
        "Consulta pública de todas las etiquetas registradas, incluidas las no asignadas. "
        "Ordenadas por nombre e ID. Sin etiquetas devuelve 200 y []. "
        "No consulta Loyverse ni requiere autenticación."
    ),
    responses={
        500: {"model": MenuErrorResponse, "description": "Error interno al consultar etiquetas."},
        503: {"model": MenuErrorResponse, "description": "PostgreSQL temporalmente no disponible."},
    },
)
def get_menu_tags(db: Session = Depends(get_db)) -> list[MenuTag]:
    """Entrega las etiquetas utilizando el servicio de lectura."""
    try:
        return menu_tags.get_menu_tags(db)
    except menu_service.MenuUnavailableError as error:
        logger.warning("Etiquetas temporalmente no disponibles (%s).", type(error.__cause__).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Las etiquetas no están disponibles temporalmente.",
        ) from None
    except Exception as error:
        cause = error.__cause__ if isinstance(error, menu_service.MenuReadError) else error
        logger.error("Falló la consulta de etiquetas (%s).", type(cause).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No fue posible consultar las etiquetas.",
        ) from None


@router.get(
    "/menu/{product_id}",
    response_model=MenuProductDetailResponse,
    summary="Consultar los detalles de un producto",
    description=(
        "Consulta pública por ID interno, basada en PostgreSQL y sin llamadas a Loyverse. "
        "Un producto inexistente o comercialmente inactivo devuelve 404. "
        "Los agotados permanecen visibles: can_order requiere producto activo y disponible "
        "con alguna variante disponible local y comercialmente. "
        "Sólo se incluyen los grupos realmente asignados a cada variante. "
        "Los complementos inactivos se muestran con is_active=false y can_order=false. "
        "Un complemento sólo tiene can_order=true si está activo y su variante puede pedirse. "
        "No existe disponibilidad independiente de complementos ni estado de activación de grupos. "
        "Las variantes se ordenan por ID; los grupos y opciones, por display_order e ID. "
        "Los importes son cadenas decimales exactas, con al menos dos decimales. "
        "El price de un complemento es un importe adicional y no se suma al rango del producto. "
        "El rango usa variantes con can_order=true; si ninguna puede pedirse, es una referencia de todas. "
        "Sin variantes, los precios son null y can_order=false. Las colecciones vacías son []. "
        "image_url devuelve una fotografía representativa o null; cada variante incluye tags y su galería images. "
        "description conserva la descripción local. "
        "No se presuponen cantidades mínimas, máximas ni obligatoriedad de selección. "
        "Los ejemplos ilustran el contrato; los datos devueltos proceden de la base."
    ),
    responses={
        404: {
            "model": MenuErrorResponse,
            "description": "Producto inexistente o comercialmente inactivo.",
            "content": {"application/json": {"example": {
                "detail": "Producto no encontrado.",
            }}},
        },
        500: {
            "model": MenuErrorResponse,
            "description": "Error interno de consulta o integridad del catálogo.",
            "content": {"application/json": {"example": {
                "detail": "No fue posible consultar el detalle del producto.",
            }}},
        },
        503: {
            "model": MenuErrorResponse,
            "description": "Conexión a PostgreSQL no disponible o tiempo de espera agotado.",
            "content": {"application/json": {"example": {
                "detail": "El detalle del producto no está disponible temporalmente.",
            }}},
        },
    },
)
def get_product_detail(
    product_id: Annotated[int, Path(ge=1, description="ID interno del producto, entero mayor que cero.")],
    db: Session = Depends(get_db),
) -> MenuProductDetailResponse:
    try:
        return menu_service.get_product_detail(db, product_id)
    except menu_service.MenuProductNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Producto no encontrado.",
        ) from None
    except menu_service.MenuUnavailableError as error:
        logger.warning(
            "Detalle del producto temporalmente no disponible (%s).", type(error.__cause__).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El detalle del producto no está disponible temporalmente.",
        ) from None
    except Exception as error:
        cause = error.__cause__ if isinstance(error, menu_service.MenuReadError) else error
        logger.error("Falló la consulta del detalle del producto (%s).", type(cause).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No fue posible consultar el detalle del producto.",
        ) from None

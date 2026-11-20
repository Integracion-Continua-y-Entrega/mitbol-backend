"""Envío público con autenticación opcional y confirmación posterior al commit."""

from dataclasses import dataclass
import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import UUID4
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_auth_service
from app.core.auth_http import cookie_name
from app.db.connection import DatabaseTargetError
from app.schemas.orders import OrderSubmissionInput, OrderSubmissionResponse
from app.services import cart_service, menu_service, order_submission_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/orders", tags=["Solicitudes de pedido"])
NO_STORE = {"Cache-Control": "no-store"}


@dataclass
class SubmissionContext:
    db: Session
    account_id: int | None = None


def submission_context(request: Request):
    config = request.app.state.settings
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "El envío de pedidos requiere JSON.", headers=NO_STORE)
    origins = config.cors_origins + (config.auth_origins if config.auth_enabled else [])
    origin = request.headers.get("origin")
    if origin is not None and origin not in origins:
        raise HTTPException(403, "Origen no autorizado.", headers=NO_STORE)
    token = request.cookies.get(cookie_name(config))
    try:
        if token is not None:
            if not config.auth_enabled:
                raise HTTPException(401, "La sesión no está disponible.", headers=NO_STORE)
            service = get_auth_service(request)
            with service.authenticated(token, request_id=uuid4(),
                    csrf_header=request.headers.get("x-csrf-token"), require_csrf=True) as context:
                yield SubmissionContext(context.db, context.principal.account_id)
        else:
            # Reutiliza el pool escritor existente cuando la autenticación está habilitada.
            factory = (request.app.state.auth_session_factory if config.auth_enabled
                       else request.app.state.session_factory)
            with factory() as db, db.begin():
                yield SubmissionContext(db)
    except (SQLAlchemyError, DatabaseTargetError) as error:
        logger.error("Falló la transacción de pedido (%s).", type(error).__name__)
        raise HTTPException(503, "No fue posible confirmar la solicitud. Reintenta con la misma clave de idempotencia.",
                            headers=NO_STORE) from None


@router.post(
    "", response_model=OrderSubmissionResponse, status_code=201,
    summary="Enviar una solicitud de pedido, pendiente de aceptación",
    description=(
        "Recibe cart con el contrato de HU-016 y data con el contrato de HU-018. "
        "Requiere Idempotency-Key (UUID v4 generado una vez por intento de compra). "
        "Repetir clave y contenido devuelve la confirmación histórica; cambiar contenido o cuenta produce 409. "
        "Sin cookie se registra como invitado; con cookie válida se asocia la cuenta y se exige X-CSRF-Token y origen de autenticación autorizado. "
        "Revalida HU-017/HU-016 y recalcula HU-019 antes de guardar. Los descuentos siguen sin integrar. "
        "El estado inicial es Solicitud recibida, pendiente de aceptación; no implica preparación ni pago."
    ),
    responses={
        401: {"description": "Sesión inválida o expirada."},
        403: {"description": "Origen o CSRF no autorizado."},
        404: {"description": "Producto inexistente o inactivo."},
        409: {"description": "Restaurante cerrado, selección no disponible o clave reutilizada con otro contenido."},
        422: {"description": "Carrito, modalidad, datos o clave inválidos."},
        503: {"description": "No pudo confirmarse la transacción; reintentar con la misma clave."},
    },
)
def submit_order(
    payload: OrderSubmissionInput, response: Response,
    idempotency_key: Annotated[UUID4, Header(alias="Idempotency-Key")],
    context: Annotated[SubmissionContext, Depends(submission_context, scope="function")],
) -> OrderSubmissionResponse:
    response.headers.update(NO_STORE)
    try:
        return order_submission_service.submit_order(context.db, payload, idempotency_key, account_id=context.account_id)
    except order_submission_service.OrderSubmissionError as error:
        raise HTTPException(error.status_code, error.detail, headers=NO_STORE) from None
    except cart_service.CartItemError as error:
        raise HTTPException(error.status_code, {"item_index": error.item_index, "message": str(error)}, headers=NO_STORE) from None
    except menu_service.MenuReadError as error:
        logger.error("Falló el catálogo al solicitar un pedido (%s).", type(error).__name__)
        raise HTTPException(503, "El catálogo no está disponible para confirmar la solicitud.", headers=NO_STORE) from None

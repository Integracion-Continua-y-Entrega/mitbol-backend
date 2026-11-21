"""Envío, seguimiento y cancelación privada; escrituras confirmadas antes de responder."""

from dataclasses import dataclass
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import UUID4
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_auth_service
from app.core.auth_http import cookie_name
from app.db.connection import DatabaseTargetError
from app.schemas.orders import (
    OrderCancellationInput, OrderCancellationResponse, OrderSubmissionInput, OrderSubmissionResponse, OrderTrackingResponse,
)
from app.services import cart_service, menu_service, order_cancellation_service, order_submission_service, order_tracking_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/orders", tags=["Solicitudes de pedido"])
NO_STORE = {"Cache-Control": "no-store"}
TRACKING_HEADERS = {**NO_STORE, "Referrer-Policy": "no-referrer"}


@dataclass
class SubmissionContext:
    db: Session
    account_id: int | None = None


def submission_context(request: Request):
    config = request.app.state.settings
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Esta operación de pedido requiere JSON.", headers=NO_STORE)
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
        message = ("No fue posible confirmar la cancelación. Consulta el estado del pedido antes de reintentar."
                   if request.url.path.endswith("/cancel") else
                   "No fue posible confirmar la solicitud. Reintenta con la misma clave de idempotencia.")
        raise HTTPException(503, message, headers=NO_STORE) from None


@router.post(
    "", response_model=OrderSubmissionResponse, status_code=201,
    summary="Enviar una solicitud de pedido, pendiente de aceptación",
    description=(
        "Recibe cart con el contrato de HU-016 y data con el contrato de HU-018. "
        "Requiere Idempotency-Key (UUID v4 generado una vez por intento de compra). "
        "Repetir clave y contenido devuelve la confirmación histórica; cambiar contenido o cuenta produce 409. "
        "Sin cookie se registra como invitado; con cookie válida se asocia la cuenta y se exige X-CSRF-Token y origen de autenticación autorizado. "
        "Revalida HU-017/HU-016 y recalcula HU-019 con los descuentos comerciales elegibles antes de guardar. "
        "Conserva el desglose histórico en applied_discounts e informa reglas pendientes en pending_discounts; no aplica cupones personales. "
        "Los invitados reciben tracking con path y token privado, estable en reintentos; conservarlo para consultar el pedido. "
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


def tracking_database(request: Request):
    factory = (request.app.state.auth_session_factory if request.app.state.settings.auth_enabled
               else request.app.state.session_factory)
    with factory() as db:
        yield db


@router.get(
    "/{order_id}/tracking", response_model=OrderTrackingResponse,
    summary="Consultar seguimiento privado de un pedido invitado",
    description=(
        "No requiere sesión. Enviar Authorization: Bearer <tracking.token> recibido al registrar el pedido. "
        "El identificador y la clave de idempotencia por sí solos no autorizan el acceso. "
        "Devuelve estado actual, modalidad y resumen histórico, sin nombre, teléfono ni datos de cuenta. "
        "No enviar el token por URL o parámetros de consulta. No modifica el pedido."
    ),
    responses={404: {"description": "Pedido o acceso privado no válidos."},
               503: {"description": "Seguimiento temporalmente no disponible."}},
)
def track_order(
    order_id: UUID, response: Response,
    db: Annotated[Session, Depends(tracking_database)],
    authorization: Annotated[str | None, Header(description="Bearer seguido del token privado del pedido.")] = None,
) -> OrderTrackingResponse:
    response.headers.update(TRACKING_HEADERS)
    scheme, _, token = (authorization or "").partition(" ")
    try:
        return order_tracking_service.track_guest_order(db, order_id, token if scheme.lower() == "bearer" else None)
    except order_tracking_service.GuestOrderAccessError:
        raise HTTPException(404, "Pedido o acceso privado no válidos.", headers=TRACKING_HEADERS) from None
    except (SQLAlchemyError, DatabaseTargetError) as error:
        logger.error("Falló la consulta de seguimiento (%s).", type(error).__name__)
        raise HTTPException(503, "El seguimiento no está disponible temporalmente.", headers=TRACKING_HEADERS) from None


@router.post(
    "/{order_id}/cancel", response_model=OrderCancellationResponse,
    summary="Cancelar una solicitud propia pendiente de aceptación",
    description=(
        "Enviar JSON vacío {}. Sin cookie, requiere Authorization: Bearer <tracking.token> del pedido invitado. "
        "Con cookie, requiere una sesión válida, X-CSRF-Token y origen de autenticación autorizado; "
        "la cuenta debe ser propietaria del pedido, independientemente de sus roles. "
        "Sólo cambia Solicitud recibida a Cancelado mediante una actualización condicional transaccional. "
        "Conserva todos los datos históricos. Rechaza pedidos aceptados, procesados o ya cancelados."
    ),
    responses={
        401: {"description": "Sesión inválida o expirada."},
        403: {"description": "Origen o CSRF no autorizado."},
        404: {"description": "Pedido o acceso privado no válidos."},
        409: {"description": "El pedido ya no está pendiente de aceptación."},
        422: {"description": "Identificador o cuerpo inválidos; enviar {}."},
        503: {"description": "No pudo confirmarse la cancelación; consultar el estado antes de reintentar."},
    },
)
def cancel_order(
    order_id: UUID, payload: OrderCancellationInput, response: Response,
    context: Annotated[SubmissionContext, Depends(submission_context, scope="function")],
    authorization: Annotated[str | None, Header(description="Bearer del pedido invitado, sin cookie de sesión.")] = None,
) -> OrderCancellationResponse:
    response.headers.update(TRACKING_HEADERS)
    scheme, _, token = (authorization or "").partition(" ")
    try:
        return order_cancellation_service.cancel_order(
            context.db, order_id, account_id=context.account_id, guest_token=token if scheme.lower() == "bearer" else None,
        )
    except order_tracking_service.GuestOrderAccessError:
        raise HTTPException(404, "Pedido o acceso privado no válidos.", headers=TRACKING_HEADERS) from None
    except order_cancellation_service.OrderNotPendingError:
        raise HTTPException(409, "El pedido ya no está pendiente de aceptación y no puede cancelarse.", headers=TRACKING_HEADERS) from None

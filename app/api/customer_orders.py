"""Pedidos propios: la sesión determina la cuenta; el cliente no envía account_id."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth_dependencies import get_current_user
from app.schemas.customer_orders import CustomerOrderDetail, CustomerOrderListResponse
from app.services import customer_order_service
from app.services.auth_service import Principal


router = APIRouter(prefix="/api/auth/orders", tags=["Mis pedidos"])
User = Annotated[Principal, Depends(get_current_user, scope="function")]


@router.get("", response_model=CustomerOrderListResponse, summary="Consultar los pedidos de la cuenta autenticada",
            description="Más recientes primero, con desempate por UUID descendente. Incluye únicamente pedidos "
                        "asociados a esta cuenta; no incorpora pedidos previos de invitado por nombre o teléfono. "
                        "El filtro status es opcional. La paginación no congela el historial entre solicitudes.")
def list_orders(request: Request, response: Response, user: User,
                status: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
                limit: Annotated[int, Query(ge=1, le=100)] = 25,
                offset: Annotated[int, Query(ge=0)] = 0):
    response.headers["Cache-Control"] = "no-store"
    return customer_order_service.list_orders(request.state.auth_context.db, user.account_id,
                                              status=status, limit=limit, offset=offset)


@router.get("/{order_id}", response_model=CustomerOrderDetail, summary="Consultar el detalle histórico de un pedido propio",
            description="Devuelve el estado actual y los productos, precios y descuentos guardados al registrar el pedido. "
                        "can_cancel informa si sigue pendiente; la cancelación existente revalida estado, propiedad y CSRF. "
                        "Admin y Staff tampoco pueden consultar aquí pedidos de otras cuentas. "
                        "Un pedido inexistente, ajeno o de invitado devuelve el mismo 404.")
def order_detail(order_id: UUID, request: Request, response: Response, user: User):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return customer_order_service.order_detail(request.state.auth_context.db, user.account_id, order_id)

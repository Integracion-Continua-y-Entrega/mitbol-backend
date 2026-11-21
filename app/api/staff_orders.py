"""Solicitudes del restaurante protegidas por permisos y CSRF existentes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth_dependencies import require_permission
from app.schemas.orders import OrderAcceptanceInput, OrderRejectionInput, StaffOrderDetail, StaffOrderListResponse
from app.services import staff_order_service
from app.services.auth_service import Principal


router = APIRouter(prefix="/api/admin/orders",tags=["Gestión de solicitudes"])
Reader = Annotated[Principal,Depends(require_permission("orders:read"))]
Writer = Annotated[Principal,Depends(require_permission("orders:write"))]


@router.get("",response_model=StaffOrderListResponse,summary="Consultar solicitudes del restaurante")
def list_orders(request: Request, response: Response, user: Reader,
                status: Annotated[str | None,Query(min_length=1,max_length=50)] = None,
                limit: Annotated[int,Query(ge=1,le=100)] = 25, offset: Annotated[int,Query(ge=0)] = 0):
    response.headers["Cache-Control"] = "no-store"
    return staff_order_service.list_orders(request.state.auth_context.db,status=status,limit=limit,offset=offset)


@router.get("/{order_id}",response_model=StaffOrderDetail,summary="Consultar pedido e historial de estados")
def order_detail(order_id: UUID, request: Request, response: Response, user: Reader):
    response.headers["Cache-Control"] = "no-store"
    return staff_order_service.order_detail(request.state.auth_context.db,order_id)


@router.post("/{order_id}/accept",response_model=StaffOrderDetail,summary="Aceptar una solicitud pendiente")
def accept_order(order_id: UUID, payload: OrderAcceptanceInput, request: Request, response: Response, user: Writer):
    response.headers["Cache-Control"] = "no-store"
    return staff_order_service.decide_order(request.state.auth_context.db,order_id,accepted=True,actor_account_id=user.account_id)


@router.post("/{order_id}/reject",response_model=StaffOrderDetail,summary="Rechazar una solicitud pendiente",
             description="Motivo opcional de hasta 500 caracteres. No altera los productos ni importes históricos.")
def reject_order(order_id: UUID, payload: OrderRejectionInput, request: Request, response: Response, user: Writer):
    response.headers["Cache-Control"] = "no-store"
    return staff_order_service.decide_order(request.state.auth_context.db,order_id,accepted=False,
                                           actor_account_id=user.account_id,reason=payload.reason)

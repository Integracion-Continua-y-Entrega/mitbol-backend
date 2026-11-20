"""Billetera personal y selección sin reserva; utiliza la sesión existente."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth_dependencies import get_current_user
from app.api.cart import _handle_cart_errors
from app.schemas.coupons import CouponListResponse, CouponSelectionInput, CouponSelectionResponse
from app.services import coupon_service
from app.services.auth_service import Principal


router = APIRouter(prefix="/api/auth/coupons", tags=["Cupones personales"])
User = Annotated[Principal, Depends(get_current_user, scope="function")]


@router.get("", response_model=CouponListResponse, summary="Consultar los cupones propios")
def list_coupons(request: Request, response: Response, user: User,
                 limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0):
    response.headers["Cache-Control"] = "no-store"
    return {"coupons": coupon_service.list_coupons(request.state.auth_context.db, user.account_id,
        now=request.app.state.auth_service.now(), limit=limit, offset=offset)}


@router.post("/select", response_model=CouponSelectionResponse, summary="Validar y seleccionar un cupón propio",
             description="Requiere sesión, Origin autorizado y X-CSRF-Token. Selección preliminar sin escrituras de cupones. "
                         "Recibe un solo customer_coupon_id, cart y modality. Recalcula HU-019. "
                         "eligible=false y selected_coupon_id=null impiden la selección. "
                         "Solo calcula beneficios fijos con alcance order y condiciones explícitas; "
                         "los porcentajes requieren reglas de redondeo pendientes. No modifica los totales de HU-021.")
def select_coupon(payload: CouponSelectionInput, request: Request, response: Response, user: User):
    response.headers["Cache-Control"] = "no-store"
    with _handle_cart_errors():
        return coupon_service.select_coupon(request.state.auth_context.db, user.account_id, payload,
                                            now=request.app.state.auth_service.now())

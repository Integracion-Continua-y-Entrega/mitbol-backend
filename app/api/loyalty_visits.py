"""La petición del personal confirma presencia física; pedidos y pagos no registran visitas."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth_dependencies import require_permission
from app.schemas.loyalty_identification import LoyaltyIdentificationInput
from app.schemas.loyalty_visits import LoyaltyVisitRead
from app.services import loyalty_visit_service
from app.services.auth_service import Principal


router = APIRouter(prefix="/api/admin/loyalty/visits", tags=["Visitas presenciales"])
Registrar = Annotated[Principal, Depends(require_permission("visits:register"))]


@router.post("", response_model=LoyaltyVisitRead, status_code=201, summary="Registrar una visita presencial validada")
def register_visit(payload: LoyaltyIdentificationInput, request: Request, response: Response, user: Registrar):
    """Admin o Staff con Origin y CSRF; recibe solo el QR presentado y registra como máximo una visita por día local."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return loyalty_visit_service.register_visit(request.state.auth_context.db, payload, actor_account_id=user.account_id,
        clock=request.app.state.auth_service.now, restaurant_timezone=request.app.state.settings.restaurant_timezone)

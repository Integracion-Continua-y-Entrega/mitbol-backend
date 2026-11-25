"""QR personal por sesión; la validación de personal no crea visitas ni emite beneficios."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth_dependencies import get_current_user, require_permission
from app.schemas.loyalty_identification import LoyaltyIdentificationInput, LoyaltyIdentificationIssue, LoyaltyIdentificationRead, LoyaltyIdentificationValidation
from app.services import loyalty_identification_service as service
from app.services.auth_service import Principal


router = APIRouter(tags=["Identificación de fidelización"])
User = Annotated[Principal, Depends(get_current_user, scope="function")]
Validator = Annotated[Principal, Depends(require_permission("visits:validate"))]


@router.post("/api/auth/loyalty/identification", response_model=LoyaltyIdentificationRead, status_code=201,
             summary="Emitir o renovar mi identificación QR")
def issue_identification(payload: LoyaltyIdentificationIssue, request: Request, response: Response, user: User):
    """Recibe {}; el código anterior deja de ser válido. No incluye identidad personal en el QR."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return service.issue_identification(request.state.auth_context, clock=request.app.state.auth_service.now)


@router.post("/api/admin/loyalty/identifications/validate", response_model=LoyaltyIdentificationValidation,
             summary="Comprobar identificación presentada al personal")
def validate_identification(payload: LoyaltyIdentificationInput, request: Request, response: Response, user: Validator):
    """Admin o Staff con Origin y CSRF; comprobar el QR no lo consume ni registra una visita."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return service.validate_identification(request.state.auth_context.db, payload, clock=request.app.state.auth_service.now)

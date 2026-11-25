"""Progreso propio: la sesión determina la identidad, también para Admin y Staff."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth_dependencies import get_current_user
from app.core.auth_errors import AuthError
from app.schemas.loyalty_progress import LoyaltyProgressRead
from app.services import loyalty_progress_service
from app.services.auth_service import Principal


router = APIRouter(prefix="/api/auth/loyalty", tags=["Progreso de fidelización"])
User = Annotated[Principal, Depends(get_current_user, scope="function")]


@router.get("/progress", response_model=LoyaltyProgressRead, summary="Consultar mi progreso de fidelización")
def progress(request: Request, response: Response, user: User):
    """No acepta parámetros de identidad ni emite recompensas; confirma la sesión antes de responder."""
    if request.query_params:
        raise AuthError(422, "La consulta de progreso no admite parámetros.", code="LOYALTY_PROGRESS_QUERY")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return loyalty_progress_service.get_progress(request.state.auth_context.db, account_id=user.account_id)

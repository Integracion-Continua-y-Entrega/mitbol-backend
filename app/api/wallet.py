"""Billetera propia de consulta; no ofrece operaciones de reserva o canje."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.auth_dependencies import get_current_user
from app.core.auth_errors import AuthError
from app.schemas.wallet import WalletRead
from app.services import wallet_service
from app.services.auth_service import Principal


router = APIRouter(prefix="/api/auth/wallet", tags=["Billetera digital"])
User = Annotated[Principal, Depends(get_current_user, scope="function")]


@router.get("", response_model=WalletRead, summary="Consultar mi billetera digital")
def wallet(request: Request, response: Response, user: User,
           limit: Annotated[int, Query(ge=1, le=100)] = 25, offset: Annotated[int, Query(ge=0)] = 0):
    """La sesión determina el propietario; solo admite limit y offset y confirma antes de responder."""
    if any(key not in {"limit", "offset"} for key in request.query_params):
        raise AuthError(422, "La billetera solo admite limit y offset.", code="WALLET_QUERY")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return wallet_service.get_wallet(request.state.auth_context.db, account_id=user.account_id,
        now=request.app.state.auth_service.now(), limit=limit, offset=offset)

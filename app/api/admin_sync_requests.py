"""Solicitud manual con autorización corta y ejecución operativa separada."""

from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.auth_dependencies import get_auth_service
from app.core.auth_errors import AuthError
from app.core.auth_http import cookie_name
from app.schemas.sync_requests import ManualSyncRequest, ManualSyncResponse, completed_response
from app.services.manual_sync_coordinator import ManualSyncUnavailableError
from app.services.sync_execution_errors import public_sync_error


def authorize_manual_sync(request: Request):
    service = get_auth_service(request)
    config = request.app.state.settings
    # No mantener los locks de cuenta/sesión mientras el operador trabaja.
    with service.authenticated(request.cookies.get(cookie_name(config)), request_id=uuid4(),
            csrf_header=request.headers.get("x-csrf-token"), require_csrf=True) as context:
        if not {"catalog:read", "settings:write"} <= context.principal.permissions:
            raise AuthError(403, "No tienes permiso para solicitar sincronizaciones.", code="INSUFFICIENT_PERMISSION")
        service.audit(context.db, "sync.manual.authorize", "success", context.request_id,
                      actor_id=context.principal.account_id, session_id=context.session_id)
        return context.principal


authorize_manual_sync.required_permission = "settings:write"
router = APIRouter(prefix="/api/admin/sync", tags=["Sincronización administrativa"])


@router.post("/requests", response_model=ManualSyncResponse)
async def request_sync(payload: ManualSyncRequest, request: Request, actor=Depends(authorize_manual_sync)):
    try:
        coordinator = getattr(request.app.state, "manual_sync_coordinator", None)
        if coordinator is None:
            raise ManualSyncUnavailableError("No se configuró un ejecutor.")
        result = await coordinator.execute(payload.target)
        return completed_response(payload.target, result)
    except Exception as error:
        status, detail = public_sync_error(error)
        return JSONResponse({"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"})

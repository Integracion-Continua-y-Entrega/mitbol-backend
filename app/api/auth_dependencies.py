"""Dependencias reutilizables. Usar scope=function para confirmar antes de responder."""

from typing import Annotated
from uuid import uuid4

from fastapi import Depends, Request

from app.core.auth_errors import AuthError
from app.core.auth_http import cookie_name, validate_auth_request
from app.services.auth_service import Principal


def get_auth_service(request: Request):
    config = request.app.state.settings
    validate_auth_request(request, config)
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise AuthError(404, "Recurso no encontrado.")
    return service


def get_current_user(request: Request):
    service = get_auth_service(request)
    config = request.app.state.settings
    # El secreto sólo llega desde la cookie elegida por configuración.
    with service.authenticated(request.cookies.get(cookie_name(config)), request_id=uuid4(),
            csrf_header=request.headers.get("x-csrf-token"), require_csrf=request.method not in {"GET", "HEAD", "OPTIONS"}) as context:
        request.state.auth_context = context
        try:
            yield context.principal
        finally:
            request.state.auth_context = None


def require_permission(permission: str):
    def dependency(user: Annotated[Principal, Depends(get_current_user, scope="function")]):
        if permission not in user.permissions:
            raise AuthError(403, "No tienes permiso para esta operación.", code="INSUFFICIENT_PERMISSION")
        return user
    dependency.required_permission = permission
    return dependency


def get_authenticated_database(
    request: Request, user: Annotated[Principal, Depends(get_current_user, scope="function")],
):
    return request.state.auth_context.db

"""Sesiones individuales; perfil local o Render validado, siempre opt-in."""

from dataclasses import asdict
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth_dependencies import get_auth_service, get_current_user
from app.core.auth_http import clear_session_cookie, cookie_name, login_rate_address, partition_session_cookie
from app.schemas.auth import CsrfResponse, LoginInput, LoginResponse, PasswordChangeInput, SessionResponse
from app.services.auth_service import Principal

router = APIRouter(prefix="/api/auth", tags=["Autenticación"])
User = Annotated[Principal, Depends(get_current_user, scope="function")]


def user_response(user):
    data = asdict(user)
    data["roles"], data["permissions"] = sorted(user.roles), sorted(user.permissions)
    return data


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginInput, request: Request, response: Response):
    config = request.app.state.settings
    service = get_auth_service(request)
    user, session, token, csrf = service.login(payload.email, payload.password.get_secret_value(),
        ip=login_rate_address(request, config), request_id=uuid4(), old_token=request.cookies.get(cookie_name(config)))
    response.set_cookie(cookie_name(config), token.value, max_age=config.auth_absolute_seconds,
                        expires=session.expires_at, secure=config.auth_cookie_secure, httponly=True,
                        samesite="none" if config.auth_cookie_partitioned else "lax", path="/")
    partition_session_cookie(response, config)
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_response(user), "expires_at": session.expires_at,
            "idle_expires_at": session.idle_expires_at, "csrf_token": csrf}


@router.get("/me", response_model=SessionResponse)
def me(request: Request, response: Response, user: User):
    session = request.state.auth_context.session
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_response(user), "expires_at": session.expires_at, "idle_expires_at": session.idle_expires_at}


@router.get("/csrf", response_model=CsrfResponse)
def csrf(request: Request, response: Response, user: User):
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": request.state.auth_context.csrf}


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: User):
    request.app.state.auth_service.logout(request.state.auth_context)
    clear_session_cookie(response, request.app.state.settings)
    response.headers["Cache-Control"] = "no-store"


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, response: Response, user: User):
    request.app.state.auth_service.logout(request.state.auth_context, all_sessions=True)
    clear_session_cookie(response, request.app.state.settings)
    response.headers["Cache-Control"] = "no-store"


@router.post("/change-password", status_code=204)
def change_password(payload: PasswordChangeInput, request: Request, response: Response, user: User):
    request.app.state.auth_service.change_password(request.state.auth_context,
        payload.current_password.get_secret_value(), payload.new_password.get_secret_value(),
        ip=login_rate_address(request, request.app.state.settings))
    clear_session_cookie(response, request.app.state.settings)
    response.headers["Cache-Control"] = "no-store"

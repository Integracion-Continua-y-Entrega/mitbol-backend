"""Sesiones individuales; perfil local o Render validado, siempre opt-in."""

from dataclasses import asdict
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request, Response

from app.api.auth_dependencies import get_auth_service, get_current_user
from app.core.auth_http import clear_session_cookie, cookie_name, login_rate_address, partition_session_cookie
from app.schemas.auth import (
    CsrfResponse, CustomerRegistrationInput, CustomerRegistrationResponse,
    LoginInput, LoginResponse, PasswordChangeInput, PasswordRecoveryInput,
    PasswordRecoveryResponse, PasswordResetInput, ProfileResponse, ProfileUpdateInput, SessionResponse,
)
from app.services.auth_service import Principal

router = APIRouter(prefix="/api/auth", tags=["Autenticación"])
User = Annotated[Principal, Depends(get_current_user, scope="function")]


def user_response(user):
    data = asdict(user)
    data["roles"], data["permissions"] = sorted(user.roles), sorted(user.permissions)
    return data


@router.post(
    "/register", response_model=CustomerRegistrationResponse, status_code=201,
    summary="Registrar una cuenta personal de cliente",
    description=(
        "Público, sin sesión previa. Requiere JSON, origen de autenticación autorizado y X-CSRF-Protection: 1. "
        "Recibe name, phone, email y password (15 a 1024 caracteres, sin recortar ni normalizar la contraseña). "
        "Normaliza correo y teléfono; crea persona y cuenta activa en una transacción. "
        "No crea empleado ni asigna roles o permisos administrativos. "
        "No inicia sesión automáticamente; continuar mediante /api/auth/login."
    ),
    responses={409: {"description": "El correo ya tiene una cuenta, incluso si está inactiva."},
               422: {"description": "Datos inválidos o campos no admitidos."},
               429: {"description": "Límite de intentos o trabajo de contraseñas alcanzado."},
               503: {"description": "No pudo confirmarse el registro."}},
)
def register(payload: CustomerRegistrationInput, request: Request, response: Response):
    config = request.app.state.settings
    user = get_auth_service(request).register(payload, ip=login_rate_address(request, config), request_id=uuid4())
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_response(user)}


@router.post("/password-recovery", response_model=PasswordRecoveryResponse, status_code=202,
             summary="Solicitar recuperación sin revelar la existencia de la cuenta",
             description="Requiere Origin autorizado y X-CSRF-Protection: 1. La entrega de correo está pendiente de integración.")
def request_password_recovery(payload: PasswordRecoveryInput, request: Request, response: Response):
    get_auth_service(request).request_password_recovery(payload.email,
        ip=login_rate_address(request, request.app.state.settings), request_id=uuid4())
    response.headers["Cache-Control"] = "no-store"
    return {"detail": "Si la cuenta está habilitada y la entrega está disponible, recibirás instrucciones de recuperación."}


@router.post("/reset-password", status_code=204, summary="Restablecer contraseña con un token privado de un solo uso",
             description="Token válido durante 30 minutos. Requiere Origin autorizado y X-CSRF-Protection: 1. Revoca todas las sesiones.")
def reset_password(payload: PasswordResetInput, request: Request, response: Response):
    get_auth_service(request).reset_password(payload.token.get_secret_value(), payload.new_password.get_secret_value(),
        ip=login_rate_address(request, request.app.state.settings), request_id=uuid4())
    clear_session_cookie(response, request.app.state.settings)
    response.headers["Cache-Control"] = "no-store"


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


@router.get("/profile", response_model=ProfileResponse, summary="Consultar el perfil propio")
def profile(request: Request, response: Response, user: User):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.auth_service.profile(request.state.auth_context)


@router.patch("/profile", response_model=ProfileResponse, summary="Actualizar los datos personales propios",
              description="Admite name y/o phone; sin campos adicionales ni nulos. El correo es de solo lectura. "
                          "Requiere sesión, Origin autorizado y X-CSRF-Token. No cambia credenciales ni permisos.")
def update_profile(payload: ProfileUpdateInput, request: Request, response: Response, user: User):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.auth_service.update_profile(request.state.auth_context, payload)


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

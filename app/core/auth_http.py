"""Barrera HTTP local o perfil Render explícito; nunca confía en IP de cabeceras."""

from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import Request
from starlette.responses import JSONResponse

from app.core.auth_errors import AuthError


def cookie_name(config):
    return "__Host-mitbol_session" if config.auth_cookie_secure else "mitbol_session_local"


def clear_session_cookie(response, config):
    response.delete_cookie(cookie_name(config), path="/", secure=config.auth_cookie_secure,
                           httponly=True, samesite="none" if config.auth_cookie_partitioned else "lax")
    partition_session_cookie(response, config)


def partition_session_cookie(response, config):
    if config.auth_cookie_partitioned:
        # Starlette exige Python 3.14 para partitioned=True. El proyecto usa 3.13;
        # añadir el atributo estándar sólo a la cookie recién emitida, sin tocar
        # Morsel global ni interpolar datos del cliente.
        key, value = response.raw_headers[-1]
        assert key == b"set-cookie"
        response.raw_headers[-1] = (key, value + b"; Partitioned")


def login_rate_address(request, config):
    # Conservador: ninguna IP/cabecera falsificada abre cuotas nuevas en Render.
    # AUTH_IP_ATTEMPTS es allí una cuota GLOBAL, además del límite por cuenta.
    return "0.0.0.0" if config.app_env == "cloud" else request.client.host


def auth_error_response(error, config):
    headers = {"Cache-Control": "no-store"}
    if error.retry_after is not None:
        headers["Retry-After"] = str(error.retry_after)
    response = JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=headers)
    if error.status_code == 401:
        clear_session_cookie(response, config)
    return response


def validate_auth_request(request, config):
    if not config.auth_enabled:
        raise AuthError(404, "Recurso no encontrado.")
    if config.app_env == "cloud":
        config.validate_auth_configuration()
        # Render fuerza HTTPS público y termina TLS antes del HTTP interno.
        # Host se valida sin usar Forwarded/X-Forwarded-Host/Proto ni peer IP.
        expected = urlsplit(config.auth_public_origin).netloc
        hosts = request.headers.getlist("host")
        origins = request.headers.getlist("origin")
        if hosts != [expected] or origins != config.auth_origins:
            raise AuthError(403, "Host u origen administrativo no autorizado.", code="ORIGIN_REJECTED")
        return
    try:
        host = urlsplit("//" + request.headers.get("host", ""))
        local = bool(request.client and ip_address(request.client.host).is_loopback
                     and host.hostname in {"localhost", "127.0.0.1", "::1"}
                     and not host.username and not host.password)
        host.port
    except ValueError:
        local = False
    if not local or any(header in request.headers for header in (
        "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    )):
        raise AuthError(403, "La autenticación de esta fase sólo admite conexión local directa.", code="LOCAL_ONLY")
    if config.auth_cookie_secure and request.url.scheme != "https":
        raise AuthError(400, "La configuración de cookie exige HTTPS.", code="HTTPS_REQUIRED")
    origin = request.headers.get("origin")
    if origin is not None and origin not in config.auth_origins:
        raise AuthError(403, "Origen no autorizado.", code="ORIGIN_REJECTED")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin not in config.auth_origins:
        raise AuthError(403, "Se requiere un origen administrativo autorizado.", code="ORIGIN_REQUIRED")


class AuthRequestGuard:
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (scope["path"] == "/api/auth" or scope["path"].startswith("/api/auth/")):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            validate_auth_request(request, self.config)
            if request.method == "POST":
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise AuthError(415, "La autenticación requiere JSON.", code="JSON_REQUIRED")
                if scope["path"] == "/api/auth/login" and request.headers.get("x-csrf-protection") != "1":
                    raise AuthError(403, "Protección de inicio de sesión requerida.", code="LOGIN_CSRF")
        except AuthError as error:
            await auth_error_response(error, self.config)(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 16_384:
                await auth_error_response(AuthError(413, "Solicitud de autenticación demasiado grande."), self.config)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        consumed = False
        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, bounded_receive, send)

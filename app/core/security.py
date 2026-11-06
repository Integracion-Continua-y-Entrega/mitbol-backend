"""Guard administrativo local: sesiones individuales o clave legada excluyente."""

from ipaddress import ip_address
from secrets import compare_digest
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from fastapi.routing import iter_route_contexts
from starlette._utils import get_route_path
from uuid import uuid4

from app.core.config import Settings

MAX_ADMIN_BODY_BYTES = 5_000_000 + 65_536
ADMIN_SECURITY_DESCRIPTION = (
    "Sólo desarrollo backend: habilitar ADMIN_ENABLED con ADMIN_API_KEY aleatoria "
    "de 32 a 128 caracteres URL-safe, distinta de Loyverse. Ejecutar Uvicorn en "
    "127.0.0.1, sin proxy ni exposición pública. Usar Authorize en este Swagger "
    "local. La clave se mantiene en configuración del backend; no introducirla "
    "en React, URLs ni archivos versionados. Se rechazan clientes remotos y "
    "orígenes distintos del propio Swagger. Es una clave compartida, sin identidad "
    "por empleado ni roles; sustituir por autenticación real antes de integrar React."
)
admin_header = APIKeyHeader(
    name="X-Admin-Key", scheme_name="LocalAdminKey", auto_error=False,
    description=ADMIN_SECURITY_DESCRIPTION,
)


def valid_admin_key(config: Settings, provided: str | None) -> bool:
    expected = config.admin_api_key.get_secret_value() if config.admin_api_key else ""
    return bool(config.admin_enabled and expected and provided and len(provided) <= 128
                and compare_digest(provided.encode("utf-8"), expected.encode("utf-8")))


def local_admin_request(request: Request) -> bool:
    try:
        if any(header in request.headers for header in (
            "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
        )):
            return False
        if request.client is None or not ip_address(request.client.host).is_loopback:
            return False
        host = request.headers.get("host", "")
        if urlsplit("//" + host).hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        origin = request.headers.get("origin")
        return origin is None or origin == f"{request.url.scheme}://{host}"
    except ValueError:
        return False


def require_local_admin(
    request: Request, key: Annotated[str | None, Security(admin_header)],
) -> None:
    config = request.app.state.settings
    if not config.admin_enabled:
        raise HTTPException(404, "Recurso no encontrado.")
    if not local_admin_request(request):
        raise HTTPException(403, "La administración sólo admite acceso backend local.")
    if not valid_admin_key(config, key):
        raise HTTPException(401, "Credencial administrativa inválida.")


class AdminRequestGuard:
    """Autoriza y limita bytes antes de que el parser multipart reciba archivos."""

    def __init__(self, app, config: Settings):
        self.app = app
        self.config = config

    def authorize_session(self, request, scope):
        from app.core.auth_errors import AuthError
        from app.core.auth_http import cookie_name

        # Obtiene los permisos de las dependencias de la ruta real, sin una
        # segunda lista de URLs que pueda quedar desactualizada.
        permissions = {"catalog:read"}
        matched = False
        for route in iter_route_contexts(request.app.routes):
            if (getattr(route, "dependant", None) is not None and route.path_regex.match(get_route_path(scope))
                    and scope["method"] in route.methods):
                matched = True
                permissions.update(permission for dep in route.dependant.dependencies
                                   if (permission := getattr(dep.call, "required_permission", None)))
                break
        if not matched:
            raise AuthError(404, "Recurso no encontrado.")
        with request.app.state.auth_service.authenticated(
            request.cookies.get(cookie_name(self.config)), request_id=uuid4(),
            csrf_header=request.headers.get("x-csrf-token"),
            require_csrf=request.method not in {"GET", "HEAD", "OPTIONS"},
        ) as context:
            if not permissions <= context.principal.permissions:
                raise AuthError(403, "No tienes permiso para esta operación.", code="INSUFFICIENT_PERMISSION")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (
            scope["path"] == "/api/admin" or scope["path"].startswith("/api/admin/")
        ):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        error = None
        if self.config.auth_enabled and not self.config.auth_catalog_enabled:
            await JSONResponse({"detail": "Recurso no encontrado."}, status_code=404)(scope, receive, send)
            return
        if self.config.auth_enabled:
            from app.core.auth_errors import AuthError
            from app.core.auth_http import auth_error_response, validate_auth_request

            try:
                validate_auth_request(request, self.config)
                await run_in_threadpool(self.authorize_session, request, scope)
            except AuthError as auth_error:
                await auth_error_response(auth_error, self.config)(scope, receive, send)
                return
            original_send = send

            async def private_send(message):
                if message["type"] == "http.response.start":
                    message = dict(message)
                    message["headers"] = [(k, v) for k, v in message["headers"] if k.lower() != b"cache-control"]
                    message["headers"].append((b"cache-control", b"no-store"))
                await original_send(message)

            send = private_send
        elif not self.config.admin_enabled:
            error = (404, "Recurso no encontrado.")
        elif not local_admin_request(request):
            error = (403, "La administración sólo admite acceso backend local.")
        elif not valid_admin_key(self.config, request.headers.get("x-admin-key")):
            error = (401, "Credencial administrativa inválida.")
        if error:
            await JSONResponse({"detail": error[1]}, status_code=error[0])(scope, receive, send)
            return
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_ADMIN_BODY_BYTES:
                await JSONResponse({"detail": "La petición supera el tamaño permitido."}, status_code=413)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)

"""Worker ASGI loopback separado. Usar factory; no se conecta al importar."""

from contextlib import asynccontextmanager
from ipaddress import ip_address
from secrets import compare_digest
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.sync_requests import ManualSyncRequest, ManualSyncResponse
from app.services.sync_execution_errors import public_sync_error
from app.services.sync_worker_runtime import SyncWorkerConfig, SyncWorkerRuntime


def create_worker(*, runtime=None):
    runtime = runtime if runtime is not None else SyncWorkerRuntime(SyncWorkerConfig.from_environment())

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(title="MITBOL sync worker", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        try:
            local = (request.client and ip_address(request.client.host).is_loopback
                     and urlsplit("//" + request.headers.get("host", "")).hostname in {"127.0.0.1", "localhost", "::1"})
        except ValueError:
            local = False
        if not local or any(h in request.headers for h in ("origin", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")):
            return JSONResponse({"detail": "Acceso operativo local requerido."}, status_code=403)
        authorization = request.headers.getlist("authorization")
        expected = "Bearer " + runtime.config.worker_token.get_secret_value()
        if len(authorization) != 1 or not authorization[0].isascii() or not compare_digest(authorization[0], expected):
            return JSONResponse({"detail": "Credencial operativa inválida."}, status_code=401)
        # Evita recibir cuerpos ilimitados incluso con Transfer-Encoding chunked.
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2048:
                return JSONResponse({"detail": "Solicitud demasiado grande."}, status_code=413)
        request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({"detail": "Solicitud operativa inválida."}, status_code=422)

    @app.post("/internal/sync/execute", response_model=ManualSyncResponse)
    async def execute(payload: ManualSyncRequest):
        try:
            return await runtime.execute(payload.target)
        except Exception as error:
            status, detail = public_sync_error(error)
            return JSONResponse({"detail": detail}, status_code=status)

    return app

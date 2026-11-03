from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.api.menu import router as menu_router
from app.api.media import router as media_router
from app.core.config import Settings, settings
from app.core.security import ADMIN_SECURITY_DESCRIPTION, AdminRequestGuard
from app.db.database import session_factory_for
from app.schemas.menu import MenuProductDetailResponse, MenuResponse
from app.services.enrichment_errors import EnrichmentError
from app.services.image_storage import LocalImageStorage

def create_app(config: Settings | None = None) -> FastAPI:
    config = config or settings
    config.validate_database_configuration()
    config.validate_admin_configuration()

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            application.state.session_factory.kw["bind"].dispose()

    application = FastAPI(
        title="API Menú Digital - mitbol",
        description=("API pública de consulta del catálogo." if config.app_env == "cloud" else
                     "Backend para la gestión del menú y sincronización con Loyverse. " + ADMIN_SECURITY_DESCRIPTION),
        version="1.0.0", swagger_ui_parameters={"persistAuthorization": False}, lifespan=lifespan,
    )
    application.state.settings = config
    application.state.session_factory = session_factory_for(config)
    application.state.image_storage = LocalImageStorage(config.image_storage_root)
    application.add_middleware(
        CORSMiddleware, allow_origins=config.cors_origins, allow_credentials=False,
        allow_methods=["GET"] if not config.admin_enabled else ["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type"] if not config.admin_enabled else ["Content-Type", "X-Admin-Key"],
    )
    application.add_middleware(AdminRequestGuard, config=config)

    @application.exception_handler(EnrichmentError)
    async def enrichment_error(request: Request, error: EnrichmentError):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    # Alembic administra el esquema. El router de sincronización sigue deshabilitado.
    application.include_router(menu_router)
    application.include_router(media_router)
    if config.admin_enabled:
        from app.api.admin_catalog import router as admin_router
        application.include_router(admin_router)

    @application.get("/")
    def read_root():
        return {"mensaje": "¡La API de mitbol está funcionando correctamente!"}

    default_openapi = application.openapi

    def openapi_with_menu_examples():
        schema = default_openapi()
        for model in (MenuResponse, MenuProductDetailResponse):
            schema["components"]["schemas"][model.__name__]["examples"] = (
                model.model_json_schema(mode="serialization")["examples"]
            )
        return schema

    application.openapi = openapi_with_menu_examples
    return application


app = create_app()

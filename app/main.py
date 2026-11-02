from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.api.menu import router as menu_router
from app.api.media import router as media_router
from app.core.config import Settings, settings
from app.core.security import ADMIN_SECURITY_DESCRIPTION, AdminRequestGuard
from app.schemas.menu import MenuProductDetailResponse, MenuResponse
from app.services.enrichment_errors import EnrichmentError
from app.services.image_storage import LocalImageStorage

def create_app(config: Settings | None = None) -> FastAPI:
    config = config or settings
    config.validate_admin_configuration()
    application = FastAPI(
        title="API Menú Digital - mitbol",
        description="Backend para la gestión del menú y sincronización con Loyverse. " + ADMIN_SECURITY_DESCRIPTION,
        version="1.0.0", swagger_ui_parameters={"persistAuthorization": False},
    )
    application.state.settings = config
    application.state.image_storage = LocalImageStorage(config.image_storage_root)
    application.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:5173"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
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

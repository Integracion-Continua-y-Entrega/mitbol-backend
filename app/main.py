from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from starlette.responses import JSONResponse

from app.api.menu import router as menu_router
from app.api.media import router as media_router
from app.api.cart import router as cart_router
from app.api.order_modality import router as order_modality_router
from app.api.orders import router as orders_router
from app.core.config import Settings, settings
from app.core.scoped_cors import ScopedCORSMiddleware
from app.core.security import ADMIN_SECURITY_DESCRIPTION, AdminRequestGuard
from app.db.database import session_factory_for
from app.schemas.menu import MenuProductDetailResponse, MenuResponse
from app.services.enrichment_errors import EnrichmentError
from app.services.image_storage import create_image_storage

def create_app(config: Settings | None = None) -> FastAPI:
    config = config or settings
    config.validate_database_configuration()
    config.validate_admin_configuration()
    config.validate_auth_configuration()
    config.validate_sync_configuration()

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            application.state.image_storage.close()
            application.state.session_factory.kw["bind"].dispose()
            if config.auth_enabled:
                application.state.auth_session_factory.kw["bind"].dispose()

    application = FastAPI(
        title="API Menú Digital - mitbol",
        description=("API pública de consulta del catálogo." if config.app_env == "cloud" and not config.auth_enabled else
                     "Administración con sesiones individuales, permisos y CSRF." if config.auth_enabled else
                     "Backend para la gestión del menú y sincronización con Loyverse. " + ADMIN_SECURITY_DESCRIPTION),
        version="1.0.0", swagger_ui_parameters={"persistAuthorization": False}, lifespan=lifespan,
    )
    application.state.settings = config
    application.state.session_factory = session_factory_for(config)
    application.state.image_storage = create_image_storage(config)
    if not config.auth_enabled:
        application.add_middleware(ScopedCORSMiddleware, config=config)
    application.add_middleware(AdminRequestGuard, config=config)
    if config.auth_enabled:
        from app.api.auth import router as auth_router
        from app.api.coupons import router as coupon_router
        from app.api.staff_orders import router as staff_order_router
        from app.api.customer_orders import router as customer_order_router
        from app.core.auth_errors import AuthError
        from app.core.auth_http import AuthRequestGuard, auth_error_response
        from app.db.auth_database import auth_session_factory_for
        from app.services.auth_service import AuthService
        from app.services.password_recovery_email import recovery_email_delivery

        application.state.auth_session_factory = auth_session_factory_for(config)
        application.state.auth_service = AuthService(application.state.auth_session_factory, config,
            recovery_delivery=recovery_email_delivery(config))
        application.include_router(auth_router)
        application.include_router(coupon_router)
        application.include_router(staff_order_router)
        application.include_router(customer_order_router)
        application.add_middleware(AuthRequestGuard, config=config)
        # CORS exterior resuelve preflight sin credenciales y decora también errores.
        application.add_middleware(ScopedCORSMiddleware, config=config)

        @application.exception_handler(AuthError)
        async def auth_error(request, error):
            return auth_error_response(error, config)

        @application.exception_handler(RequestValidationError)
        async def auth_validation(request, error):
            if request.url.path.startswith(("/api/auth/", "/api/admin/sync/")):
                message = ("Solicitud de sincronización inválida." if request.url.path.startswith("/api/admin/sync/")
                           else "Solicitud de autenticación inválida.")
                return JSONResponse({"detail": message}, status_code=422,
                                    headers={"Cache-Control": "no-store"})
            return await request_validation_exception_handler(request, error)

    @application.exception_handler(EnrichmentError)
    async def enrichment_error(request: Request, error: EnrichmentError):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    # Alembic administra el esquema; el router legado de sincronización no se registra.
    application.include_router(menu_router)
    application.include_router(media_router)
    application.include_router(cart_router)
    application.include_router(order_modality_router)
    application.include_router(orders_router)
    if config.auth_sync_enabled:
        from app.api.admin_sync import router as sync_router
        application.include_router(sync_router)
    if config.admin_enabled or (config.auth_enabled and config.auth_catalog_enabled):
        from app.api.admin_catalog import create_admin_router
        application.include_router(create_admin_router(authenticated=config.auth_enabled,
            images_enabled=config.auth_images_enabled if config.auth_enabled else True))
        from app.api.admin_discounts import create_discount_router
        from app.models import Discount
        application.include_router(create_discount_router(Discount.__table__, authenticated=config.auth_enabled))

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

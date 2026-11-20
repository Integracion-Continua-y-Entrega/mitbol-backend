"""Conserva CORS público separado de los orígenes de administración con cookie."""

from starlette.middleware.cors import CORSMiddleware


class ScopedCORSMiddleware:
    def __init__(self, app, config):
        legacy_admin = config.admin_enabled and not config.auth_enabled
        self.public = CORSMiddleware(app, allow_origins=config.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"] if legacy_admin else ["GET"],
            allow_headers=["Content-Type", "X-Admin-Key"] if legacy_admin else ["Content-Type"])
        self.private = (CORSMiddleware(app, allow_origins=config.auth_origins, allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Protection", "X-CSRF-Token"], expose_headers=["Retry-After"])
            if config.auth_enabled else self.public)
        self.public_validation = CORSMiddleware(app, allow_origins=config.cors_origins,
            allow_credentials=False, allow_methods=["POST"], allow_headers=["Content-Type"])
        self.orders = CORSMiddleware(app,
            allow_origins=list(dict.fromkeys(config.cors_origins + (config.auth_origins if config.auth_enabled else []))),
            allow_credentials=config.auth_enabled, allow_methods=["POST"],
            allow_headers=["Content-Type", "Idempotency-Key", "X-CSRF-Token"])
        self.order_tracking = CORSMiddleware(app,
            allow_origins=list(dict.fromkeys(config.cors_origins + (config.auth_origins if config.auth_enabled else []))),
            allow_credentials=False, allow_methods=["GET"], allow_headers=["Authorization"])

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path.startswith("/api/orders/") and path.endswith("/tracking"):
            await self.order_tracking(scope, receive, send)
            return
        if path == "/api/orders":
            await self.orders(scope, receive, send)
            return
        if path in {"/api/cart/validate", "/api/cart/summary", "/api/order-modality/validate"}:
            await self.public_validation(scope, receive, send)
            return
        private = any(path == prefix or path.startswith(prefix + "/") for prefix in ("/api/auth", "/api/admin"))
        await (self.private if private else self.public)(scope, receive, send)

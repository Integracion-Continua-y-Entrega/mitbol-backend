"""Conserva CORS público separado de los orígenes de administración con cookie."""

from starlette.middleware.cors import CORSMiddleware


class ScopedCORSMiddleware:
    def __init__(self, app, config):
        self.public = CORSMiddleware(app, allow_origins=config.cors_origins,
                                     allow_credentials=False, allow_methods=["GET"], allow_headers=["Content-Type"])
        self.private = CORSMiddleware(app, allow_origins=config.auth_origins, allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Protection", "X-CSRF-Token"], expose_headers=["Retry-After"])

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        private = any(path == prefix or path.startswith(prefix + "/") for prefix in ("/api/auth", "/api/admin"))
        await (self.private if private else self.public)(scope, receive, send)

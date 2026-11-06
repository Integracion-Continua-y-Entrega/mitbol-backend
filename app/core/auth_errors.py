"""Errores públicos controlados; nunca incluyen entradas ni errores SQL."""


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str, *, code: str = "ACCESS_DENIED", retry_after: int | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.retry_after = retry_after
        self.actor_id = None
        self.session_id = None
        self.revoke = False


def invalid_session() -> AuthError:
    return AuthError(401, "La sesión no es válida o ha expirado.", code="INVALID_SESSION")

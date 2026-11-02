"""Errores públicos estables, sin detalles de PostgreSQL ni rutas del servidor."""


class EnrichmentError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class StorageError(EnrichmentError):
    def __init__(self):
        super().__init__(503, "El almacenamiento de imágenes no está disponible temporalmente.")

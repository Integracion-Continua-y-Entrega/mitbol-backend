"""Errores de dominio de la sincronización comercial, sin mensajes del servidor."""


class CatalogSyncError(RuntimeError):
    pass


class CatalogValidationError(CatalogSyncError):
    pass


class CatalogReferenceError(CatalogValidationError):
    pass


class CatalogTargetError(CatalogSyncError):
    pass


class CatalogSyncBusyError(CatalogSyncError):
    pass


class CatalogPersistenceError(CatalogSyncError):
    pass

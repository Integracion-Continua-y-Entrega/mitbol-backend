"""Traducción compartida de fallos, sin publicar mensajes del proveedor o SQL."""

from app.db.connection import DatabaseTargetError
from app.services.catalog_errors import CatalogSyncBusyError, CatalogTargetError, CatalogValidationError
from app.services.discount_sync_service import DiscountSyncBusyError
from app.services.discount_transform import DiscountValidationError
from app.services.loyverse_client import LoyverseAPIError
from app.services.manual_sync_coordinator import ManualSyncUnavailableError


def public_sync_error(error: Exception) -> tuple[int, str]:
    if isinstance(error, (CatalogSyncBusyError, DiscountSyncBusyError)):
        return 409, "Hay otra operación comercial en curso; espera antes de solicitar una nueva."
    if isinstance(error, (DatabaseTargetError, CatalogTargetError, ManualSyncUnavailableError)):
        return 503, "La ejecución de sincronización no está disponible con la configuración actual."
    if isinstance(error, (LoyverseAPIError, CatalogValidationError, DiscountValidationError)):
        return 502, "No se pudo obtener información comercial válida de Loyverse."
    return 503, "No se pudo confirmar el resultado; verifica el estado antes de repetir la solicitud."

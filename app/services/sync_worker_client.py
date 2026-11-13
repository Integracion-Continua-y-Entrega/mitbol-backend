"""Delegación HTTP al worker local; no recibe credenciales comerciales."""

import re
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.schemas.sync_requests import InternalSyncRequest, InternalSyncResponse
from app.schemas.sync_destination import SyncDestination
from app.services.catalog_errors import CatalogSyncBusyError
from app.services.manual_sync_coordinator import ManualSyncCoordinator, ManualSyncUnavailableError, SyncDestinationMismatchError
from app.services.loyverse_client import LoyverseAPIError


def validate_worker_url(value: str) -> str:
    try:
        url = urlsplit(value)
        valid = (value == value.strip() and not any(c.isspace() for c in value)
                 and "\\" not in value and url.scheme in {"http", "https"}
                 and url.hostname in {"127.0.0.1", "localhost", "::1"}
                 and url.username is None and url.password is None and "?" not in value and "#" not in value
                 and url.path in {"", "/"} and (url.port is None or 1 <= url.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("SYNC_WORKER_URL requiere un origen loopback explícito, sin credenciales o rutas.")
    return value.rstrip("/")


def validate_worker_token(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
        raise ValueError("SYNC_WORKER_TOKEN requiere un secreto independiente URL-safe de 32 a 128 caracteres.")
    return value


class SyncWorkerClient:
    def __init__(self, url: str, token: SecretStr, *, destination: SyncDestination, timeout_seconds=120, transport=None):
        self.url = validate_worker_url(url)
        validate_worker_token(token.get_secret_value())
        self.token, self.timeout, self.transport = token, timeout_seconds, transport
        self.destination = SyncDestination.model_validate(destination)

    async def execute(self, target):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5), transport=self.transport,
                follow_redirects=False, trust_env=False,
            ) as client:
                payload = InternalSyncRequest(target=target, destination=self.destination)
                response = await client.post(self.url + "/internal/sync/execute",
                    headers={"Authorization": "Bearer " + self.token.get_secret_value()}, json=payload.model_dump(mode="json"))
            if response.status_code == 412:
                raise SyncDestinationMismatchError("El worker rechazó el destino solicitado.")
            if response.status_code == 409:
                raise CatalogSyncBusyError("Ejecución ocupada.")
            if response.status_code == 502:
                raise LoyverseAPIError("El worker no obtuvo datos válidos del proveedor.")
            if response.status_code != 200:
                raise ManualSyncUnavailableError("El worker no confirmó la operación.")
            result = InternalSyncResponse.model_validate(response.json())
            if result.destination != self.destination:
                raise SyncDestinationMismatchError("No se pudo confirmar un resultado del destino autorizado.")
            if result.target != target:
                raise ManualSyncUnavailableError("El worker devolvió otro proceso.")
            return result.result.model_dump() if target == "catalog" else result.result.discounts_processed
        except (CatalogSyncBusyError, ManualSyncUnavailableError, LoyverseAPIError):
            raise
        except Exception:
            raise ManualSyncUnavailableError("No se pudo confirmar el resultado del worker; verifica antes de repetir.") from None

    def coordinator(self):
        return ManualSyncCoordinator(
            catalog_runner=lambda: self.execute("catalog"),
            discounts_runner=lambda: self.execute("discounts"),
        )

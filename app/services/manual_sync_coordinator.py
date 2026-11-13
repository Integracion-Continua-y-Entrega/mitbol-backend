"""HU-010: despacho interno sin sesiones web o credenciales implícitas."""

from collections.abc import Awaitable, Callable
from typing import Literal


SyncRunner = Callable[[], Awaitable[dict | int]]


class ManualSyncUnavailableError(RuntimeError):
    """No hay un ejecutor configurado; no se intenta una alternativa insegura."""


class SyncDestinationMismatchError(ManualSyncUnavailableError):
    """El destino autorizado y el del ejecutor no coinciden."""


class ManualSyncCoordinator:
    """Selecciona callbacks configurados por el servidor, no por el JSON.

    Cada callback debe usar el servicio comercial correspondiente y sus propios
    recursos operativos; ese servicio controla bloqueo, destino y transacción.
    Este coordinador no recibe sesiones web, tokens o URLs desde la solicitud.
    """

    def __init__(
        self, *, catalog_runner: SyncRunner | None = None,
        discounts_runner: SyncRunner | None = None,
    ):
        self._runners = {"catalog": catalog_runner, "discounts": discounts_runner}

    async def execute(self, target: Literal["catalog", "discounts"]) -> dict | int:
        if not isinstance(target, str) or target not in self._runners:
            raise ValueError("Proceso de sincronización no admitido.")
        runner = self._runners[target]
        if runner is None:
            raise ManualSyncUnavailableError("La ejecución manual no está configurada para este proceso.")

        resultado: dict | int = await runner()

        return resultado

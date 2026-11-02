"""Cliente de lectura de Loyverse: HTTP, paginación y errores, sin persistencia."""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable

import httpx
from pydantic import SecretStr
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt


class LoyverseAPIError(Exception):
    """Error seguro para mostrar: no incluye credenciales ni cuerpos HTTP."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class LoyverseConfigurationError(LoyverseAPIError):
    pass


class LoyverseResponseError(LoyverseAPIError):
    pass


class LoyverseTransientError(LoyverseAPIError):
    def __init__(self, message: str, *, status_code=None, retry_after=None):
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class RateLimitException(LoyverseTransientError):
    """Conserva la excepción del cliente original para HTTP 429."""


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not Decimal(str(seconds)).is_finite():
        return None
    return max(0.0, seconds)


def _reject_json_constant(value: str):
    raise ValueError("Constante numérica no válida en JSON")


class LoyverseAPIClient:
    BASE_URL = "https://api.loyverse.com/v1.0"
    # Compatibilidad del prototipo: el diagnóstico no consulta /categories.
    READ_ENDPOINTS = {"/stores", "/items", "/modifiers", "/variants", "/categories"}

    def __init__(
        self,
        access_token: str | SecretStr | None = None,
        *,
        page_size: int = 250,
        max_attempts: int = 3,
        timeout_seconds: float = 15.0,
        max_retry_wait_seconds: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if access_token is None:
            from app.core.config import settings

            access_token = settings.loyverse_access_token
        token = access_token.get_secret_value() if isinstance(access_token, SecretStr) else access_token
        if not token or not token.strip():
            raise LoyverseConfigurationError("Configura LOYVERSE_ACCESS_TOKEN.")
        if not 1 <= page_size <= 250 or max_attempts < 1:
            raise ValueError("Paginación o número de intentos fuera de rango.")
        if timeout_seconds <= 0 or max_retry_wait_seconds <= 0:
            raise ValueError("Los tiempos de espera deben ser positivos.")

        self.page_size = page_size
        self.max_attempts = max_attempts
        self.max_retry_wait_seconds = max_retry_wait_seconds
        self._sleep = sleep
        self.request_count = 0
        self.page_counts: dict[str, int] = {}
        self._http = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {token.strip()}"},
            timeout=httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds)),
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _retry_wait(self, retry_state) -> float:
        error = retry_state.outcome.exception()
        if error.retry_after is not None:
            # No reintentar antes de Retry-After ni dejar una espera ilimitada.
            if error.retry_after > self.max_retry_wait_seconds:
                raise LoyverseAPIError(
                    "Loyverse requiere una espera mayor al límite configurado; consulta pospuesta.",
                    status_code=error.status_code,
                )
            return error.retry_after
        return min(2 ** (retry_state.attempt_number - 1), 10.0)

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        if endpoint not in self.READ_ENDPOINTS:
            raise ValueError("Ruta de lectura no admitida por el cliente de Loyverse.")
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=self._retry_wait,
            retry=retry_if_exception_type(LoyverseTransientError),
            sleep=self._sleep,
            reraise=True,
        ):
            with attempt:
                return await self._get_once(endpoint, params)
        raise AssertionError("El control de reintentos terminó sin resultado")

    async def _get_once(self, endpoint: str, params: dict | None) -> dict:
        self.request_count += 1
        try:
            response = await self._http.get(endpoint, params=params)
        except httpx.TransportError:
            raise LoyverseTransientError(
                f"Fallo de conexión o tiempo de espera al consultar {endpoint}."
            ) from None

        status = response.status_code
        if status in (408, 429) or 500 <= status < 600:
            error_type = RateLimitException if status == 429 else LoyverseTransientError
            raise error_type(
                f"Loyverse respondió HTTP {status} al consultar {endpoint}.",
                status_code=status,
                retry_after=_retry_after_seconds(response.headers.get("Retry-After")),
            )
        if status != 200:
            hints = {
                401: "Revisa la vigencia del token.",
                402: "Revisa el acceso a la API y la suscripción de Loyverse.",
                403: "Revisa los permisos ITEMS_READ y STORES_READ.",
            }
            raise LoyverseAPIError(
                f"Loyverse respondió HTTP {status} al consultar {endpoint}. {hints.get(status, '')}".strip(),
                status_code=status,
            )
        try:
            data = json.loads(
                response.content, parse_float=Decimal, parse_constant=_reject_json_constant,
            )
        except (ValueError, UnicodeDecodeError):
            raise LoyverseResponseError(f"JSON inválido en {endpoint}.") from None
        if not isinstance(data, dict):
            raise LoyverseResponseError(f"Se esperaba un objeto JSON en {endpoint}.")
        return data

    @staticmethod
    def _collection(data: dict, key: str) -> list[dict]:
        records = data.get(key)
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise LoyverseResponseError(f"La respuesta no contiene una lista válida '{key}'.")
        return records

    async def get_all_paginated(
        self, endpoint: str, data_key: str, params: dict | None = None,
    ) -> list[dict]:
        all_records = []
        base_params = {"limit": self.page_size, **(params or {})}
        cursor = base_params.pop("cursor", None)
        seen_cursors = {cursor} if cursor else set()
        for page_number in range(1, 1001):
            page_params = {**base_params, **({"cursor": cursor} if cursor else {})}
            data = await self.get(endpoint, params=page_params)
            records = self._collection(data, data_key)
            next_cursor = data.get("cursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise LoyverseResponseError(f"Cursor inválido en {endpoint}.")
            if next_cursor and (next_cursor in seen_cursors or not records):
                raise LoyverseResponseError(f"Paginación incompleta o cursor repetido en {endpoint}.")
            all_records.extend(records)
            if not next_cursor:
                self.page_counts[endpoint] = page_number
                return all_records
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise LoyverseResponseError(f"Se excedió el límite de páginas en {endpoint}.")

    async def get_stores(self) -> list[dict]:
        # /stores no documenta limit/cursor: no se inventa paginación para él.
        data = await self.get("/stores")
        stores = self._collection(data, "stores")
        if data.get("cursor"):
            raise LoyverseResponseError("La lista de tiendas devolvió paginación no documentada.")
        self.page_counts["/stores"] = 1
        return stores

    async def get_items(self, *, include_deleted: bool = False) -> list[dict]:
        # Las variantes ya vienen anidadas en /items: no se duplican consultas.
        params = {"show_deleted": "true"} if include_deleted else None
        return await self.get_all_paginated("/items", "items", params)

    async def get_modifiers(self, *, include_deleted: bool = False) -> list[dict]:
        # Las opciones de cada grupo vienen en modifier_options.
        params = {"show_deleted": "true"} if include_deleted else None
        return await self.get_all_paginated("/modifiers", "modifiers", params)

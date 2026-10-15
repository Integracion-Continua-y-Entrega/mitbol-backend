import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.core.config import settings

class RateLimitException(Exception):
    """Excepción lanzada cuando se supera el límite de peticiones de la API."""
    pass

class LoyverseAPIClient:
    def __init__(self):
        self.base_url = "https://api.loyverse.com/v1.0"
        self.headers = {
            "Authorization": f"Bearer {settings.loyverse_access_token}"
        }

    # Reintenta hasta 5 veces, esperando 2^x * 1 segundo entre intentos (máximo 10s de espera)
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(RateLimitException)
    )
    async def get(self, endpoint: str, params: dict = None):
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}{endpoint}",
                headers=self.headers,
                params=params
            )
            
            if response.status_code == 429:
                raise RateLimitException("Límite de peticiones de Loyverse excedido")
                
            response.raise_for_status()
            return response.json()

    async def get_all_paginated(self, endpoint: str, data_key: str) -> list:
        """
        Realiza peticiones en bucle hasta extraer todo el catálogo usando el cursor.
        data_key es 'categories' o 'items'.
        """
        all_records = []
        cursor = None

        while True:
            # Enviamos el cursor en los parámetros solo si existe
            params = {"cursor": cursor} if cursor else None
            response_data = await self.get(endpoint, params=params)

            # Agregamos los registros de esta página a nuestra lista principal
            if data_key in response_data:
                all_records.extend(response_data[data_key])

            # Buscamos el cursor para la siguiente página
            cursor = response_data.get("cursor")
            
            # Si Loyverse ya no devuelve un cursor, rompemos el ciclo
            if not cursor:
                break

        return all_records
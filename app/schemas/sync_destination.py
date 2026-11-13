"""Identidad lógica del catálogo, sin usuarios, contraseñas o token del proveedor."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.connection import local_database_url, postgresql_url


def canonical_host(host: str) -> str:
    host = host.lower()
    # Solo el alias pooled de un endpoint Neon conocido representa el mismo
    # destino directo. No unir por semejanza hosts o dominios arbitrarios.
    labels = host.split(".")
    if host.endswith(".neon.tech") and labels[0].startswith("ep-") and labels[0].endswith("-pooler"):
        labels[0] = labels[0][:-7]
        return ".".join(labels)
    return host


class SyncDestination(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    environment: Literal["local", "test", "cloud"]
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1)
    store_id: str

    @field_validator("host")
    @classmethod
    def canonical_endpoint(cls, value):
        return canonical_host(value)

    @field_validator("store_id")
    @classmethod
    def canonical_store(cls, value):
        try:
            return str(UUID(value))
        except ValueError:
            raise ValueError("La sincronización requiere un UUID de tienda válido.") from None

    @classmethod
    def from_url(cls, url, *, environment: str, store_id: str):
        url = postgresql_url(url) if environment == "cloud" else local_database_url(url)
        return cls(environment=environment, host=url.host or "", port=url.port or 5432,
                   database=url.database or "", store_id=store_id)

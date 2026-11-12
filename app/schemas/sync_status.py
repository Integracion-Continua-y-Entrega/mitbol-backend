"""HU-010: resumen observado por entidad, sin historial de ejecuciones."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class SyncEntity(str, Enum):
    PRODUCTS = "Products"
    VARIANTS = "ProductVariants"
    MODIFIERS = "ProductModifiers"
    COMPLEMENTS = "ProductComplements"
    DISCOUNTS = "Discounts"


class EntitySyncSummary(BaseModel):
    entity: SyncEntity
    total_count: int = Field(ge=0, strict=True)
    synced_count: int = Field(ge=0, strict=True)
    last_synced_at: datetime | None

    @model_validator(mode="after")
    def consistent_observation(self):
        """Las tres agregaciones deben describir el mismo conjunto de filas."""
        if self.synced_count > self.total_count:
            raise ValueError("El conteo con fecha no puede superar el total.")
        if (self.synced_count == 0) != (self.last_synced_at is None):
            raise ValueError("La fecha debe existir exactamente cuando hay registros con fecha.")
        if self.last_synced_at is not None:
            if self.last_synced_at.utcoffset() is None:
                raise ValueError("La fecha observada requiere zona horaria.")
            self.last_synced_at = self.last_synced_at.astimezone(timezone.utc)
        return self

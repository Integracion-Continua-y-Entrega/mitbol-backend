"""Contratos HU-010 para React; nunca incluyen credenciales o payloads HTTP."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SyncError(BaseModel):
    code: str
    message: str


class EntityChanges(BaseModel):
    new: int = Field(ge=0)
    updated: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    retired: int = Field(ge=0)
    not_returned: int = Field(ge=0)


class AssignmentChanges(BaseModel):
    inserted: int = Field(ge=0)
    removed: int = Field(ge=0)


class CatalogChanges(BaseModel):
    products: EntityChanges
    variants: EntityChanges
    modifiers: EntityChanges
    complements: EntityChanges
    assignments: AssignmentChanges


class SyncResult(BaseModel):
    changes: CatalogChanges
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_at: datetime


class SyncRunResponse(BaseModel):
    run_id: UUID
    source: Literal["manual", "scheduled", "webhook"]
    status: Literal["queued", "running", "retry_wait", "succeeded", "failed"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    available_at: datetime
    attempts: int
    result: SyncResult | None
    error: SyncError | None


class SyncAccepted(BaseModel):
    accepted: bool
    created: bool
    run: SyncRunResponse


class SyncHistory(BaseModel):
    runs: list[SyncRunResponse]
    next_offset: int | None


class SyncStatus(BaseModel):
    last_catalog_synced_at: datetime | None
    catalog_locked: bool
    worker_connected: bool
    is_running: bool
    recovery_pending: bool
    active_run: SyncRunResponse | None
    last_run: SyncRunResponse | None
    last_successful_run: SyncRunResponse | None
    history_scope: Literal["managed_runs"]
    webhooks_enabled: Literal[False]

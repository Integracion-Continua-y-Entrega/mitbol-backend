"""HU-010: autoriza y encola; ninguna petición HTTP descarga el catálogo."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select

from app.api.auth_dependencies import get_authenticated_database, require_permission
from app.models.sync_run import SyncRun
from app.schemas.sync import SyncAccepted, SyncHistory, SyncRequest, SyncRunResponse, SyncStatus
from app.services.admin_transaction import audit_success
from app.services.catalog_errors import CatalogSyncBusyError, CatalogTargetError
from app.services import sync_jobs

router = APIRouter(prefix="/api/admin/sync", tags=["Sincronizaciones"],
    dependencies=[Depends(require_permission("sync:read"))])


def queue_database(request: Request, db=Depends(get_authenticated_database)):
    config = request.app.state.settings
    try:
        sync_jobs.inspect_queue(db, expected_database=config.expected_database,
            expected_host=config.expected_database_host, permission="enqueue" if request.method == "POST" else "read")
    except CatalogTargetError:
        raise HTTPException(503, "La cola de sincronización no está preparada para este destino.") from None
    return db


@router.get("/status", response_model=SyncStatus)
def sync_status(db=Depends(queue_database)):
    return sync_jobs.status(db)


@router.post("/runs", response_model=SyncAccepted, status_code=202,
             dependencies=[Depends(require_permission("sync:execute"))])
def start_sync(payload: SyncRequest, request: Request, response: Response, db=Depends(queue_database)):
    try:
        row, created = sync_jobs.enqueue(db, store_id=request.app.state.settings.loyverse_store_id,
                                        source="manual", requested_by=request.state.auth_context.principal.account_id)
    except (CatalogSyncBusyError, CatalogTargetError):
        raise HTTPException(409, "La cola está ocupada o pertenece a otra configuración; consulta su estado.") from None
    audit_success(db, "sync.request", "sync_run", row["run_id"])
    response.headers["Location"] = f"/api/admin/sync/runs/{row['run_id']}"
    return {"accepted": True, "created": created, "run": row}


@router.get("/runs", response_model=SyncHistory)
def sync_history(limit: Annotated[int, Query(ge=1, le=100)] = 20,
                 offset: Annotated[int, Query(ge=0, le=10000)] = 0, db=Depends(queue_database)):
    rows = list(db.scalars(select(SyncRun).order_by(SyncRun.created_at.desc(), SyncRun.run_id.desc())
                           .offset(offset).limit(limit + 1)))
    return {"runs": [sync_jobs.public_run(row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None}


@router.get("/runs/{run_id}", response_model=SyncRunResponse)
def sync_run(run_id: UUID, db=Depends(queue_database)):
    row = db.get(SyncRun, run_id)
    if row is None:
        raise HTTPException(404, "Ejecución no encontrada.")
    return sync_jobs.public_run(row)

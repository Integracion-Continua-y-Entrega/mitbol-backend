"""HU-010: consulta administrativa de observaciones comerciales almacenadas."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_authenticated_database, require_permission
from app.core.security import require_local_admin
from app.db.database import get_db
from app.schemas.sync_status import SyncStatusResponse
from app.services.product_enrichment_service import database_errors
from app.services.sync_status_service import get_sync_summaries
from app.services.sync_activity import sync_execution_is_active


def create_sync_status_router(*, authenticated: bool = False) -> APIRouter:
    database_dependency = get_authenticated_database if authenticated else get_db
    router = APIRouter(
        prefix="/api/admin/sync", tags=["Sincronización administrativa"],
        dependencies=[Depends(
            require_permission("catalog:read") if authenticated else require_local_admin,
        )],
    )

    @router.get("/status", response_model=SyncStatusResponse)
    def get_sync_status(db: Session = Depends(database_dependency)):
        with database_errors(db):
            return SyncStatusResponse(
                entities=get_sync_summaries(db),
                execution_active=sync_execution_is_active(db),
            )

    return router

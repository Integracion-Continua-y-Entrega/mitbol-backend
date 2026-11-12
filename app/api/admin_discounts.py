"""Router HU-009 en preparación; todavía no montado en la aplicación."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Table
from sqlalchemy.orm import Session

from app.api.auth_dependencies import get_authenticated_database, require_permission
from app.core.security import require_local_admin
from app.db.database import get_db
from app.schemas.discounts import DiscountRead
from app.services.discount_read_service import list_discounts
from app.services.product_enrichment_service import database_errors


def create_discount_router(table: Table, *, authenticated: bool = False) -> APIRouter:
    """Recibe la tabla del esquema ya integrado; reutiliza la seguridad existente."""
    database_dependency = get_authenticated_database if authenticated else get_db
    router = APIRouter(
        prefix="/api/admin", tags=["Administración"],
        dependencies=[Depends(
            require_permission("catalog:read") if authenticated else require_local_admin,
        )],
    )

    @router.get(
        "/discounts", response_model=list[DiscountRead],
        summary="Consultar descuentos comerciales almacenados",
    )
    def get_discounts(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        db: Session = Depends(database_dependency),
    ):
        with database_errors(db):
            return list_discounts(db=db, table=table, limit=limit, offset=offset)

    return router

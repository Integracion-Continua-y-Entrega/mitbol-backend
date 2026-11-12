"""Lectura administrativa local: no consulta ni modifica Loyverse."""

from sqlalchemy import Table, inspect
from sqlalchemy.orm import Session

from app.schemas.discounts import DiscountRead
from app.services.discount_queries import build_discount_list
from app.services.enrichment_errors import EnrichmentError


def list_discounts(
    db: Session, table: Table, *, limit: int = 50, offset: int = 0,
) -> list[DiscountRead]:
    """Lee datos locales; no modifica ni confirma la sesión del llamador.

    La integración externa deberá validar permisos, destino y revisión.
    Un resultado sin coincidencias se representa con una lista vacía.
    """
    statement = build_discount_list(table, limit=limit, offset=offset)
    # El rol web no puede leer alembic_version. Consultar pg_catalog evita
    # ampliar sus ACL solo para diagnosticar una instalación anterior.
    if not inspect(db.connection()).has_table("Discounts", schema="public"):
        raise EnrichmentError(503, "La consulta de descuentos requiere actualizar el esquema de la base.")

    rows = db.execute(statement).mappings().all()
    return [DiscountRead.model_validate(row) for row in rows]

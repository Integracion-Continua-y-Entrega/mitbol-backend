"""Lectura administrativa interna, pendiente de conexión al router protegido."""

from sqlalchemy import Table
from sqlalchemy.orm import Session

from app.schemas.discounts import DiscountRead
from app.services.discount_queries import build_discount_list


def list_discounts(
    db: Session, table: Table, *, limit: int = 50, offset: int = 0,
) -> list[DiscountRead]:
    """Lee datos locales; no modifica ni confirma la sesión del llamador.

    La integración externa deberá validar permisos, destino y revisión.
    Un resultado sin coincidencias se representa con una lista vacía.
    """
    statement = build_discount_list(table, limit=limit, offset=offset)

    rows = db.execute(statement).mappings().all()
    return [DiscountRead.model_validate(row) for row in rows]

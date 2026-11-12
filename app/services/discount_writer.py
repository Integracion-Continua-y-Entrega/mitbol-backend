"""Escritura interna: requiere una transacción y un destino ya validados."""

from sqlalchemy import Table
from sqlalchemy.orm import Session

from app.services.discount_statements import build_discount_upsert


def write_discount_batch(
    session: Session, table: Table, rows: tuple[dict, ...],
) -> int:
    """Ejecuta el lote sin apropiarse del commit o rollback del llamador.

    No es una entrada de sincronización: el servicio externo deberá validar
    destino, revisión, tienda y exclusión mutua antes de llamar esta función.
    El resultado cuenta filas procesadas, no inserciones frente a cambios.
    """
    if not session.in_transaction():
        raise ValueError("La escritura de descuentos requiere una transacción activa.")

    # Construir todo antes de ejecutar evita escribir si una fila no se puede
    # convertir en sentencia. Un error SQL posterior debe propagarse al llamador.
    statements = tuple(build_discount_upsert(table, row) for row in rows)

    for stmt in statements:
        session.execute(stmt)

    return len(statements)

"""HU-010: marcador transaccional de ejecución y observación sin bloqueo."""

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session


# Distinto del bloqueo comercial, que también usan las ediciones locales.
# Ambos sincronizadores la mantienen mientras su transacción está activa.
SYNC_EXECUTION_LOCK_KEY = 0x4D4954425359


def mark_sync_execution(db: Session) -> bool:
    """Marca una transacción ya autorizada, después del bloqueo comercial.

    PostgreSQL libera la marca con la transacción; no se hace commit aquí.
    """
    if not db.in_transaction():
        raise ValueError("La marca de sincronización requiere una transacción activa.")
    return db.scalar(select(func.pg_try_advisory_xact_lock(SYNC_EXECUTION_LOCK_KEY)))


def sync_execution_is_active(db: Session) -> bool:
    """Lee el marcador de esta base sin adquirir ni liberar bloqueos.

    No representa historial, última ejecución o resultado. Es una observación
    de esta base en el momento de consultar, no una garantía de trabajo futuro.
    """
    statement = text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_locks AS locks
            WHERE locks.locktype = 'advisory'
              AND locks.database = (
                  SELECT oid FROM pg_catalog.pg_database
                  WHERE datname = pg_catalog.current_database()
              )
              AND locks.classid = :lock_class
              AND locks.objid = :lock_object
              AND locks.objsubid = 1
              AND locks.mode = 'ExclusiveLock'
              AND locks.granted
        )
    """)
    parameters = {
        "lock_class": SYNC_EXECUTION_LOCK_KEY >> 32,
        "lock_object": SYNC_EXECUTION_LOCK_KEY & 0xFFFFFFFF,
    }
    return db.execute(statement, params=parameters).scalar_one()

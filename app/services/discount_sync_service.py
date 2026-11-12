"""HU-009: coordinación transaccional explícita, sin sesión global ni endpoint."""

from contextlib import contextmanager
import re
from typing import Callable
from uuid import UUID

from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.connection import CloudDatabaseTarget, environment_mode, validate_local_connection
from app.services.discount_batch import prepare_discount_batch
from app.services.discount_probe import DiscountSnapshot, fetch_discount_snapshot
from app.services.discount_writer import write_discount_batch
from app.services.loyverse_client import LoyverseAPIClient
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY
from app.services.sync_activity import mark_sync_execution


DISCOUNT_SCHEMA_REVISION = "20261111_0006"


class DiscountSyncError(ValueError):
    """Error de coordinación seguro para mostrar sin URL, payload ni SQL."""


class DiscountSyncBusyError(DiscountSyncError):
    """El destino está ocupado; no se inició otra importación."""


class DiscountSyncService:
    def __init__(
        self, session_factory: Callable[[], Session], *, expected_database: str,
        store_id: str, cloud_target: CloudDatabaseTarget | None = None,
    ):
        if cloud_target is not None:
            if expected_database != cloud_target.expected_database:
                raise DiscountSyncError("El destino no coincide con la autorización cloud.")
        elif environment_mode() == "cloud":
            raise DiscountSyncError("La sincronización cloud requiere un destino autorizado.")
        elif expected_database not in {"mitbol_dev", "mitbol_auth_local"} and not re.fullmatch(
            r"mitbol_test_(?:hu008_[0-9a-f]{12}|auth_[0-9a-f]{32})", expected_database,
        ):
            raise DiscountSyncError("Se requiere mitbol_dev, mitbol_auth_local o una base aislada de pruebas.")
        try:
            self.store_id = str(UUID(store_id))
        except (ValueError, TypeError, AttributeError):
            raise DiscountSyncError("Se requiere un UUID de tienda válido.") from None
        self.session_factory = session_factory
        self.expected_database = expected_database
        self.cloud_target = cloud_target

    @contextmanager
    def _transaction(self):
        with self.session_factory() as session, session.begin():
            if session.get_bind().dialect.name != "postgresql":
                raise DiscountSyncError("La sincronización de descuentos requiere PostgreSQL.")
            connection = session.connection()
            if self.cloud_target is None:
                validate_local_connection(connection)
            else:
                self.cloud_target.validate_connection(connection)
            if session.scalar(select(func.current_database())) != self.expected_database:
                raise DiscountSyncError("La conexión real no coincide con la base autorizada.")
            if MigrationContext.configure(connection, opts={"version_table_schema": "public"}).get_current_heads() != (DISCOUNT_SCHEMA_REVISION,):
                raise DiscountSyncError("La sincronización requiere la revisión 20261111_0006.")
            # Compartido con catálogo: impide importar fotografías comerciales
            # concurrentes en distinto orden. Se libera al cerrar la transacción.
            if not session.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
                raise DiscountSyncBusyError("Ya hay una sincronización comercial en curso.")
            if not mark_sync_execution(session):
                raise DiscountSyncBusyError("Ya hay una ejecución de sincronización activa.")
            yield session

    def _write(self, session: Session, snapshot: DiscountSnapshot) -> int:
        rows = prepare_discount_batch(snapshot, self.store_id)
        table = Table("Discounts", MetaData(), schema="public", autoload_with=session.connection())
        return write_discount_batch(session, table, rows)

    def persist_snapshot(self, snapshot: DiscountSnapshot) -> int:
        """Persiste una fotografía validada completa; no consulta al proveedor.

        Cuenta registros procesados, no solo cambios. No borra por ausencia.
        Puntos se conservan como tipo original con valor nulo, sin cálculo.
        """
        try:
            with self._transaction() as session:
                processed = self._write(session, snapshot)
            return processed
        except SQLAlchemyError:
            raise DiscountSyncError("Falló la persistencia de descuentos; verifica antes de reintentar.") from None

    async def synchronize(self, client: LoyverseAPIClient) -> int:
        """Valida destino y bloquea antes de descargar; confirma solo al terminar."""
        try:
            with self._transaction() as session:
                snapshot = await fetch_discount_snapshot(client, include_deleted=True)
                processed = self._write(session, snapshot)
            return processed
        except SQLAlchemyError:
            raise DiscountSyncError("Falló la persistencia de descuentos; verifica antes de reintentar.") from None

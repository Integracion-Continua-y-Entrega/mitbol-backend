"""HU-009: coordinación transaccional explícita, sin sesión global ni endpoint."""

from contextlib import contextmanager
import re
from typing import Callable
from uuid import UUID

from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData, Table, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.connection import CloudDatabaseTarget, LOOPBACK_HOSTS, environment_mode, validate_local_connection
from app.services.discount_batch import prepare_discount_batch
from app.services.discount_probe import DiscountSnapshot, fetch_discount_snapshot
from app.services.discount_quarantine import DiscountQuarantineError, write_quarantine_report
from app.services.discount_transform import DiscountValidationError, discount_identity
from app.services.discount_writer import write_discount_batch
from app.services.loyverse_client import LoyverseAPIClient
from app.db.schema_compatibility import validate_bridge_schema
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY


DISCOUNT_SCHEMA_REVISION = "20261111_0006"
DISCOUNT_COMPATIBLE_REVISIONS = frozenset({DISCOUNT_SCHEMA_REVISION, "20261114_0007", "20261115_0008"})


class DiscountSyncError(ValueError):
    """Error de coordinación seguro para mostrar sin URL, payload ni SQL."""


class DiscountSyncService:
    def __init__(
        self, session_factory: Callable[[], Session], *, expected_database: str,
        store_id: str, cloud_target: CloudDatabaseTarget | None = None,
        allow_partial: bool = False, quarantine_dir=None,
    ):
        if cloud_target is not None:
            if expected_database != cloud_target.expected_database:
                raise DiscountSyncError("El destino no coincide con la autorización cloud.")
        elif environment_mode() == "cloud":
            raise DiscountSyncError("La sincronización cloud requiere un destino autorizado.")
        elif expected_database != "mitbol_dev" and not re.fullmatch(
            r"mitbol_test_hu008_[0-9a-f]{12}", expected_database,
        ):
            raise DiscountSyncError("Se requiere mitbol_dev o una base aislada de pruebas.")
        try:
            self.store_id = str(UUID(store_id))
        except (ValueError, TypeError, AttributeError):
            raise DiscountSyncError("Se requiere un UUID de tienda válido.") from None
        self.session_factory = session_factory
        self.expected_database = expected_database
        self.cloud_target = cloud_target
        if allow_partial and quarantine_dir is None:
            raise DiscountSyncError("El modo parcial requiere un destino privado de cuarentena explícito.")
        self.allow_partial, self.quarantine_dir = allow_partial, quarantine_dir
        self.last_report = {}

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
                validate_bridge_schema(connection)
            if session.scalar(select(func.current_database())) != self.expected_database:
                raise DiscountSyncError("La conexión real no coincide con la base autorizada.")
            heads = MigrationContext.configure(connection, opts={"version_table_schema": "public"}).get_current_heads()
            if len(heads) != 1 or heads[0] not in DISCOUNT_COMPATIBLE_REVISIONS:
                raise DiscountSyncError("La sincronización requiere 20261111_0006 o su extensión explícitamente compatible 20261114_0007/20261115_0008.")
            if self.cloud_target is None and heads[0] == "20261115_0008":
                validate_bridge_schema(connection)
            # Compartido con catálogo: impide importar fotografías comerciales
            # concurrentes en distinto orden. Se libera al cerrar la transacción.
            if not session.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
                raise DiscountSyncError("Ya hay una sincronización comercial en curso.")
            yield session

    def _write(self, session: Session, snapshot: DiscountSnapshot) -> int:
        rows = prepare_discount_batch(snapshot, self.store_id)
        table = Table("Discounts", MetaData(), schema="public", autoload_with=session.connection())
        omitted = {discount_identity({"id": issue["loyverse_id"]}) for issue in snapshot.quarantined}
        accepted = {record.loyverse_id for record in snapshot.records}
        if accepted & omitted:
            raise DiscountValidationError("Un ID no puede estar aceptado y aislado en el mismo lote.")
        if self.allow_partial and (not snapshot.includes_deleted or snapshot.verified_store_id != self.store_id):
            raise DiscountSyncError("El modo parcial requiere descarga completa y tienda verificada con Loyverse.")
        existing_omitted = False
        if omitted:
            # También reconoce representaciones UUID históricas con otro casing/formato.
            local_ids = {discount_identity({"id": value})
                         for value in session.scalars(select(table.c.loyverse_id))}
            existing_omitted = bool(omitted & local_ids)
        rejection = ("DISCOUNT_OMITTED_ID_EXISTS" if existing_omitted else
                     "DISCOUNT_STRICT_REJECTED" if omitted and not self.allow_partial else None)
        diagnostics = dict(source_records=snapshot.source_count if snapshot.source_count is not None else len(rows),
            valid_records=len(rows), isolated_ids=len(omitted), resolved_ids=len(snapshot.resolutions),
            partial=bool(omitted), quarantine_report_written=False)
        if self.allow_partial or omitted or snapshot.resolutions:
            if self.quarantine_dir is None:
                raise DiscountSyncError("Se requiere un informe privado para conservar los descuentos aislados o resueltos.")
            revision = MigrationContext.configure(session.connection(), opts={
                "version_table_schema": "public"}).get_current_heads()[0]
            url = session.get_bind().url
            isolated_test = (environment_mode() == "test" and url.host in LOOPBACK_HOSTS
                and bool(re.fullmatch(r"mitbol_test_(?:hu008_[0-9a-f]{12}|auth_[0-9a-f]{32})", self.expected_database)))
            try:
                identifier = write_quarantine_report(self.quarantine_dir, dict(
                    fetched_at=snapshot.fetched_at.isoformat(),
                    context=dict(database=self.expected_database, store_id=self.store_id, revision=revision,
                                 mode="partial_authorized" if self.allow_partial else "strict"),
                    counts={key: diagnostics[key] for key in (
                        "source_records", "valid_records", "isolated_ids", "resolved_ids")},
                    decision=rejection or "prepared",
                    quarantined=snapshot.quarantined, resolutions=snapshot.resolutions,
                    accepted_ids=sorted(accepted)), isolated_test=isolated_test)
            except DiscountQuarantineError as error:
                raise DiscountSyncError(str(error)) from None
            diagnostics.update(quarantine_report_written=True, quarantine_report_id=identifier)
        if rejection:
            raise DiscountValidationError("Un ID aislado ya existe en Discounts; se rechaza el lote."
                if existing_omitted else "El modo estricto rechaza los descuentos aislados; no se aplicaron cambios.")
        self._prepared_report = diagnostics
        return write_discount_batch(session, table, rows)

    def persist_snapshot(self, snapshot: DiscountSnapshot) -> int:
        """Persiste una fotografía validada completa; no consulta al proveedor.

        Cuenta registros procesados, no solo cambios. No borra por ausencia.
        Puntos se conservan como tipo original con valor nulo, sin cálculo.
        """
        try:
            self.last_report = {}
            with self._transaction() as session:
                processed = self._write(session, snapshot)
            self.last_report = self._prepared_report
            return processed
        except SQLAlchemyError:
            raise DiscountSyncError("Falló la persistencia de descuentos; verifica antes de reintentar.") from None

    async def synchronize(self, client: LoyverseAPIClient) -> int:
        """Valida destino y bloquea antes de descargar; confirma solo al terminar."""
        try:
            self.last_report = {}
            with self._transaction() as session:
                snapshot = await fetch_discount_snapshot(client, include_deleted=True,
                    store_id=self.store_id, collect_issues=True)
                processed = self._write(session, snapshot)
            self.last_report = self._prepared_report
            return processed
        except SQLAlchemyError:
            raise DiscountSyncError("Falló la persistencia de descuentos; verifica antes de reintentar.") from None

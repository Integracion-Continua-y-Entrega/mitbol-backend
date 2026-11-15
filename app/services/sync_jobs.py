"""Cola PostgreSQL recuperable. Ningún trabajo depende de BackgroundTasks."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.db.connection import DatabaseTargetError
from app.models import Product, ProductComplement, ProductModifier, ProductVariant
from app.models.sync_run import SyncRun
from app.services.catalog_errors import (
    CatalogPersistenceError, CatalogSyncBusyError, CatalogTargetError, CatalogValidationError,
)
from app.services.loyverse_client import (
    LoyverseAPIError, LoyverseConfigurationError, LoyverseResponseError, LoyverseTransientError,
)
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY

SYNC_JOBS_REVISION = "20261114_0007"
QUEUE_LOCK_KEY = 0x4D4954424F50
WORKER_LOCK_KEY = 0x4D4954424F51
ACTIVE = ("queued", "running", "retry_wait")
WORKER_UPDATE_COLUMNS = ("status", "started_at", "finished_at", "available_at", "attempts",
                         "attempt_token", "result", "error_code")
ERRORS = {
    "provider_temporary": "Loyverse no está disponible temporalmente.",
    "provider_rejected": "Loyverse rechazó la lectura; revisa acceso y permisos.",
    "validation": "Destino, autorización o catálogo inválido; revisa la configuración.",
    "database": "Falló PostgreSQL; el trabajador comprobará el estado persistente.",
    "interrupted": "Se interrumpió el trabajador; se agotó el límite de recuperación.",
    "unexpected": "La ejecución falló; requiere revisión técnica.",
}


class JobFencedError(RuntimeError):
    """Otra instancia recuperó este intento: revierte también la escritura comercial."""


def inspect_queue(db, *, expected_database, expected_host, permission="read"):
    connection = db.connection()
    if connection.dialect.name != "postgresql":
        raise CatalogTargetError("La cola requiere PostgreSQL.")
    identity = db.execute(text("SELECT current_database(), current_user, session_user")).one()
    if (connection.engine.url.database != expected_database or connection.engine.url.host != expected_host
            or identity != (expected_database, connection.engine.url.username, connection.engine.url.username)):
        raise CatalogTargetError("La cola no coincide con el destino y usuario autorizados.")
    if MigrationContext.configure(connection, opts={"version_table_schema": "public"}).get_current_heads() != (SYNC_JOBS_REVISION,):
        raise CatalogTargetError("HU-010 requiere su migración y revisión verificadas.")
    valid = db.scalar(text("""
        SELECT c.relkind = 'r' AND NOT c.relrowsecurity
           AND pg_catalog.has_table_privilege(current_user, c.oid, 'SELECT')
        FROM pg_catalog.pg_class c WHERE c.oid = 'public."SyncRuns"'::regclass
    """))
    if not valid:
        raise CatalogTargetError("La cola no es una tabla pública legible sin RLS.")
    if permission in {"enqueue", "worker"}:
        for column in ("run_id", "store_id", "source", "requested_by"):
            if not db.scalar(text("SELECT has_column_privilege(current_user, 'public.\"SyncRuns\"', :column, 'INSERT')"),
                             {"column": column}):
                raise CatalogTargetError("Faltan permisos para encolar sincronizaciones.")
    if permission == "worker":
        for column in WORKER_UPDATE_COLUMNS:
            if not db.scalar(text(
                "SELECT has_column_privilege(current_user, 'public.\"SyncRuns\"', :column, 'UPDATE')"
            ), {"column": column}):
                raise CatalogTargetError("Faltan permisos del trabajador de sincronizaciones.")


def public_run(row):
    # Lista explícita: no serializar tokens de intento, usuarios técnicos o URLs.
    return {"run_id": row.run_id, "source": row.source, "status": row.status,
            "created_at": row.created_at, "started_at": row.started_at, "finished_at": row.finished_at,
            "available_at": row.available_at, "attempts": row.attempts, "result": row.result or None,
            "error": {"code": row.error_code, "message": ERRORS.get(row.error_code, "Error de sincronización.")}
                     if row.error_code else None}


def enqueue(db, *, store_id, source, requested_by=None):
    if source not in {"manual", "scheduled"}:
        raise CatalogValidationError("La recepción webhook permanece deshabilitada.")
    if not db.scalar(select(func.pg_try_advisory_xact_lock(QUEUE_LOCK_KEY))):
        raise CatalogSyncBusyError("La cola está ocupada; consulta el estado y reintenta.")
    existing = db.scalar(select(SyncRun).where(SyncRun.status.in_(ACTIVE)))
    if existing is not None:
        if existing.store_id != store_id:
            raise CatalogTargetError("Ya existe un trabajo autorizado para otra tienda.")
        return public_run(existing), False
    row = db.execute(insert(SyncRun).values(run_id=uuid4(), store_id=store_id, source=source,
                                          requested_by=requested_by).returning(SyncRun)).scalar_one()
    return public_run(row), True


def catalog_locked(db, key=CATALOG_SYNC_LOCK_KEY):
    return bool(db.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks
        WHERE locktype = 'advisory' AND granted AND objsubid = 1
          AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND classid = :high AND objid = :low)
    """), {"high": key >> 32, "low": key & 0xFFFFFFFF}))


def status(db):
    latest = db.scalar(select(SyncRun).order_by(SyncRun.created_at.desc(), SyncRun.run_id.desc()).limit(1))
    success = db.scalar(select(SyncRun).where(SyncRun.status == "succeeded")
                        .order_by(SyncRun.finished_at.desc()).limit(1))
    active = db.scalar(select(SyncRun).where(SyncRun.status.in_(ACTIVE)))
    dates = [db.scalar(select(func.max(model.synced_at)))
             for model in (Product, ProductVariant, ProductModifier, ProductComplement)]
    locked, worker = catalog_locked(db), catalog_locked(db, WORKER_LOCK_KEY)
    return {"last_catalog_synced_at": max((date for date in dates if date), default=None),
            "catalog_locked": locked, "worker_connected": worker,
            "is_running": locked or bool(active and active.status == "running" and worker),
            "recovery_pending": bool(active and active.status == "running" and not worker),
            "active_run": public_run(active) if active else None,
            "last_run": public_run(latest) if latest else None,
            "last_successful_run": public_run(success) if success else None,
            "history_scope": "managed_runs", "webhooks_enabled": False}


@contextmanager
def worker_lock(engine):
    # Bloqueo de sesión, independiente de las transacciones de catálogo/historial.
    # Se libera por PostgreSQL al morir el proceso; nunca devolverlo vivo al pool.
    with engine.connect() as connection:
        acquired = connection.scalar(select(func.pg_try_advisory_lock(WORKER_LOCK_KEY)))
        connection.commit()
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(select(func.pg_advisory_unlock(WORKER_LOCK_KEY)))
                    connection.commit()
                except Exception:
                    connection.invalidate()


def failure_kind(error):
    if isinstance(error, (CatalogValidationError, CatalogTargetError, DatabaseTargetError,
                          LoyverseConfigurationError, LoyverseResponseError)):
        return "validation", False
    if isinstance(error, LoyverseAPIError):
        temporary = (isinstance(error, LoyverseTransientError) or error.status_code in {408, 429}
                     or (error.status_code is not None and 500 <= error.status_code < 600))
        return ("provider_temporary", True) if temporary else ("provider_rejected", False)
    if isinstance(error, (CatalogPersistenceError, SQLAlchemyError)):
        return "database", True
    return "unexpected", False


class SyncJobWorker:
    def __init__(self, runner, *, clock=None):
        self.runner = runner
        self.sessions = runner.session_factory
        self.engine = self.sessions.kw["bind"]
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def inspect(self, db):
        inspect_queue(db, expected_database=self.runner.expected_database,
                      expected_host=self.runner.expected_host, permission="worker")

    def claim(self):
        with self.sessions.begin() as db:
            self.inspect(db)
            db.execute(select(func.pg_advisory_xact_lock(QUEUE_LOCK_KEY)))
            row = db.scalar(select(SyncRun).where(SyncRun.status.in_(ACTIVE)).with_for_update())
            if row is None or (row.status != "running" and row.available_at > self.clock()):
                return None
            if row.store_id != self.runner.store_id:
                raise CatalogTargetError("El trabajador no está autorizado para la tienda encolada.")
            if row.source not in {"manual", "scheduled"}:
                raise CatalogTargetError("El procesamiento de webhooks permanece deshabilitado.")
            # Un running solo se recupera tras obtener el bloqueo exclusivo del trabajador.
            if row.attempts >= 3:
                row.status, row.finished_at, row.error_code = "failed", self.clock(), "interrupted"
                return row.run_id, None
            row.status, row.attempts, row.attempt_token = "running", row.attempts + 1, uuid4()
            row.started_at = row.started_at or self.clock()
            return row.run_id, row.attempt_token

    @staticmethod
    def success(db, identifier, token, report):
        result = {key: report[key] for key in ("changes", "catalog_sha256", "snapshot_at")}
        updated = db.execute(update(SyncRun).where(SyncRun.run_id == identifier,
            SyncRun.status == "running", SyncRun.attempt_token == token).values(
                status="succeeded", finished_at=datetime.fromisoformat(report["finished_at"]),
                result=result, error_code=None).returning(SyncRun.run_id)).scalar_one_or_none()
        if updated is None:
            raise JobFencedError("El intento ya fue recuperado; se revierte el catálogo.")

    def failed(self, identifier, token, error):
        with self.sessions.begin() as db:
            row = db.scalar(select(SyncRun).where(SyncRun.run_id == identifier).with_for_update())
            if row.status != "running" or row.attempt_token != token:
                return public_run(row)  # Incluye commit incierto ya confirmado en PostgreSQL.
            if isinstance(error, CatalogSyncBusyError):
                row.attempts -= 1
                row.status, row.available_at = "retry_wait", self.clock() + timedelta(seconds=30)
            else:
                code, retry = failure_kind(error)
                row.error_code = code
                if retry and row.attempts < 3:
                    row.status = "retry_wait"
                    row.available_at = self.clock() + timedelta(seconds=min(300, 30 * 2 ** (row.attempts - 1)))
                else:
                    row.status, row.finished_at = "failed", self.clock()
            db.flush()
            return public_run(row)

    async def run_once(self, client, *, approval, scheduled=False):
        with worker_lock(self.engine) as acquired:
            if not acquired:
                return {"outcome": "busy"}
            # Sin HTTP ni escrituras: valida operador y aprobación antes de tocar la cola.
            self.runner.check_target(approval=approval)
            if scheduled:
                with self.sessions.begin() as db:
                    self.inspect(db)
                    enqueue(db, store_id=self.runner.store_id, source="scheduled")
            claim = self.claim()
            if claim is None:
                return {"outcome": "idle"}
            identifier, token = claim
            if token is None:
                with self.sessions() as db:
                    return {"outcome": "processed", "run": public_run(db.get(SyncRun, identifier))}
            try:
                await self.runner.run(client, dry_run=False, approval=approval,
                    record_success=lambda db, report: self.success(db, identifier, token, report))
            except JobFencedError:
                return {"outcome": "superseded"}
            except Exception as error:
                return {"outcome": "processed", "run": self.failed(identifier, token, error)}
            with self.sessions() as db:
                return {"outcome": "processed", "run": public_run(db.get(SyncRun, identifier))}

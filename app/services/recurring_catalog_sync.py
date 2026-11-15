"""Orquestación recurrente reutilizable por CLI, scheduler y un futuro HU-010.

No lee variables, importa FastAPI ni registra tareas/endpoints. Recibe destino,
sesiones, cliente HTTP y autorización explícitos; reutiliza SyncService.
"""

from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from app.db.connection import LOOPBACK_HOSTS
from app.services.catalog_changes import catalog_changes
from app.services.catalog_errors import CatalogTargetError, CatalogValidationError
from app.services.catalog_manifest import catalog_manifest, manifest_sha256
from app.services.catalog_safety import inspect_catalog_operator
from app.services.sync_service import SyncService, validate_catalog_revision


APPROVAL_OPERATION = "catalog_recurrent_v1"


def validate_approval(approval: dict | None, scope: dict) -> None:
    expected = {"operation": APPROVAL_OPERATION, "approved": True, **scope}
    if not isinstance(approval, dict) or approval != expected or approval.get("approved") is not True:
        raise CatalogTargetError("La ejecución requiere una aprobación recurrente explícita del host, base, usuario, tienda y revisión actuales.")


class RecurringCatalogSync:
    def __init__(self, session_factory, *, expected_database: str, expected_host: str,
                 store_id: str, cloud_target=None, auth_catalog: bool = False):
        if cloud_target is None and expected_host not in LOOPBACK_HOSTS:
            raise CatalogTargetError("El destino remoto requiere autorización cloud y TLS verificado.")
        if cloud_target is not None and (cloud_target.expected_host != expected_host
                                        or cloud_target.expected_database != expected_database):
            raise CatalogTargetError("El destino recurrente no coincide con la autorización cloud.")
        if not store_id or store_id != store_id.strip() or any(char.isspace() for char in store_id):
            raise CatalogValidationError("La tienda recurrente debe ser explícita y no contener espacios.")
        self.session_factory = session_factory
        self.expected_database, self.expected_host = expected_database, expected_host
        self.store_id, self.cloud_target = store_id, cloud_target
        self.auth_catalog = auth_catalog

    def _inspect_session(self, session, *, dry_run, approval):
        connection = session.connection()
        if connection.engine.url.host != self.expected_host:
            raise CatalogTargetError("El host real no coincide con el destino recurrente esperado.")
        revision = validate_catalog_revision(connection)
        operator = inspect_catalog_operator(connection)
        if operator["user"] in {"mitbol_render_reader", "mitbol_admin_writer"}:
            raise CatalogTargetError("La sincronización requiere un operador comercial independiente de los roles web.")
        scope = {"host": self.expected_host, "database": self.expected_database,
                 "user": operator["user"], "store_id": self.store_id, "revision": revision}
        if not dry_run:
            validate_approval(approval, scope)
        return scope

    def check_target(self, *, approval):
        """Preflight sin HTTP/DML para el consumidor de la cola HU-010."""
        scope = {}
        service = SyncService(self.session_factory, expected_database=self.expected_database,
            store_id=self.store_id, cloud_target=self.cloud_target, auth_catalog=self.auth_catalog,
            before_fetch=lambda session: scope.update(self._inspect_session(
                session, dry_run=False, approval=approval)))
        with service._transaction(read_only=True):
            pass
        return scope

    async def run(self, client, *, dry_run: bool = True, approval: dict | None = None,
                  record_success=None) -> dict:
        started = datetime.now(timezone.utc)
        tick = monotonic()
        preflight, preview = {}, {}

        def before_fetch(session):
            preflight.update(self._inspect_session(session, dry_run=dry_run, approval=approval))

        def inspect_plan(session, snapshot, plan):
            preview.update(changes=catalog_changes(session, plan),
                           catalog_sha256=manifest_sha256(catalog_manifest(plan, snapshot.store)),
                           snapshot_at=plan.synced_at.isoformat())
            return preview

        def report(persisted):
            return {
                "status": "dry_run" if dry_run else "synchronized", "writes": not dry_run,
                "run_id": uuid4().hex, "started_at": started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((monotonic() - tick) * 1000),
                "target": preflight, **preview, "persistence": persisted, "errors": [],
                "approval_template": {"operation": APPROVAL_OPERATION, "approved": False, **preflight},
            }

        committed_report = {}

        def after_write(session, persisted):
            committed_report.update(report(persisted))
            if record_success is not None:
                record_success(session, committed_report)

        service = SyncService(self.session_factory, expected_database=self.expected_database,
            store_id=self.store_id, cloud_target=self.cloud_target,
            before_fetch=before_fetch, before_write=inspect_plan,
            after_write=after_write, auth_catalog=self.auth_catalog)
        if dry_run:
            await service.preview(client, inspect_plan)
            return report(None)
        else:
            await service.synchronize(client)
            return committed_report

"""HU-010: consume un trabajo durable; --enqueue-scheduled prepara polling externo.

No carga .env ni inicia un scheduler. Requiere --execute y la misma autorización
de alcance del CLI HU-008, revisada para el esquema HU-010.
"""

import argparse
import asyncio
from datetime import datetime
import json
from uuid import UUID

from sqlalchemy.orm import sessionmaker

from app.cli.sync_catalog_recurrent import configuration
from app.db.connection import DatabaseTargetError, create_database_engine
from app.services.catalog_errors import CatalogSyncBusyError, CatalogTargetError
from app.services.loyverse_client import LoyverseAPIClient
from app.services.recurring_catalog_sync import RecurringCatalogSync
from app.services.sync_jobs import SyncJobWorker, failure_kind


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("Tipo inesperado en informe de sincronización.")


async def execute(engine, target, arguments, token, approval):
    runner = RecurringCatalogSync(sessionmaker(bind=engine), expected_database=arguments.expected_database,
        expected_host=arguments.expected_host, store_id=arguments.store_id,
        cloud_target=target, auth_catalog=True)
    async with LoyverseAPIClient(access_token=token) as client:
        if arguments.dry_run:
            return await runner.run(client, dry_run=True)
        return await SyncJobWorker(runner).run_once(client, approval=approval, scheduled=arguments.enqueue_scheduled)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--store-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="Simula HU-008 con el perfil local HU-010; no encola ni escribe historial.")
    parser.add_argument("--enqueue-scheduled", action="store_true",
                        help="Encola/coalesce una actualización programada antes de consumir la cola.")
    arguments = parser.parse_args(argv)
    engine = None
    try:
        if not (arguments.execute or arguments.dry_run) or (arguments.dry_run and arguments.enqueue_scheduled):
            raise CatalogTargetError("Elige --dry-run o --execute; programar un trabajo requiere --execute.")
        url, target, token, approval = configuration(arguments)
        engine = create_database_engine(url, target=target)
        report = asyncio.run(execute(engine, target, arguments, token, approval))
        code = exit_code(report)
    except CatalogSyncBusyError:
        report, code = {"outcome": "busy"}, 3
    except Exception as error:
        category, _ = failure_kind(error)
        if isinstance(error, (CatalogTargetError, DatabaseTargetError)):
            category = "validation"
        code = {"validation": 2, "provider_temporary": 4, "provider_rejected": 4, "database": 5}.get(category, 6)
        report = {"outcome": "error", "error": {"code": category}, "verification_required": True}
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps({**report, "exit_code": code}, default=json_value, ensure_ascii=True))
    return code


def exit_code(report):
    if report.get("outcome") == "busy":
        return 3
    run = report.get("run", {})
    if run.get("status") == "retry_wait":
        return 4
    if run.get("status") == "failed":
        return {"validation": 2, "provider_rejected": 4, "provider_temporary": 4,
                "database": 5}.get((run.get("error") or {}).get("code"), 6)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

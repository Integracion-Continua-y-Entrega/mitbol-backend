"""HU-009: preflight por defecto, escritura solo con --execute. No carga .env."""

import argparse
import asyncio
import json
import os
from uuid import UUID

from alembic.runtime.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.connection import (
    CloudDatabaseTarget, DatabaseTargetError, create_database_engine, environment_mode,
    local_database_url, validate_local_connection,
)
from app.services.discount_sync_service import DISCOUNT_SCHEMA_REVISION, DiscountSyncError, DiscountSyncService
from app.services.discount_transform import DiscountValidationError
from app.services.loyverse_client import LoyverseAPIClient, LoyverseAPIError


def preflight(engine, *, expected_database: str, cloud_target=None) -> dict:
    """Inspección de destino, revisión y ACL sin DML ni consumo de secuencias."""
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        if cloud_target is None:
            validate_local_connection(connection)
        else:
            cloud_target.validate_connection(connection)
        if connection.scalar(select(func.current_database())) != expected_database:
            raise DiscountSyncError("La conexión real no coincide con la base autorizada.")
        identity = connection.execute(text("SELECT current_user, session_user")).one()
        if identity[0] != identity[1] or identity[0] != engine.url.username:
            raise DiscountSyncError("Se requiere la identidad operativa directa, sin cambio de rol.")
        readonly, recovery = connection.execute(text(
            "SELECT current_setting('default_transaction_read_only'), pg_catalog.pg_is_in_recovery()"
        )).one()
        if readonly != "off" or recovery:
            raise DiscountSyncError("El operador no debe heredar solo lectura ni apuntar a una réplica.")
        if int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 != 18:
            raise DiscountSyncError("La operación requiere PostgreSQL 18.")
        context = MigrationContext.configure(connection, opts={"version_table_schema": "public"})
        if context.get_current_heads() != (DISCOUNT_SCHEMA_REVISION,):
            raise DiscountSyncError("La sincronización requiere la revisión 20261111_0006.")
        valid = connection.scalar(text("""
            SELECT relkind = 'r' AND NOT relrowsecurity FROM pg_catalog.pg_class
            WHERE oid = pg_catalog.to_regclass('public."Discounts"')
        """))
        if not valid:
            raise DiscountSyncError("Se requiere la tabla Discounts aprobada, sin RLS.")
        for operation, columns in {
            "SELECT": ("discount_id", "loyverse_id", "name", "discount_type", "value", "scope", "is_active", "synced_at", "created_at"),
            "INSERT": ("loyverse_id", "name", "discount_type", "value", "scope", "is_active", "synced_at"),
            "UPDATE": ("name", "discount_type", "value", "scope", "is_active", "synced_at"),
        }.items():
            for column in columns:
                if not connection.scalar(text(
                    "SELECT pg_catalog.has_column_privilege(current_user, 'public.\"Discounts\"', :column, :operation)"
                ), {"column": column, "operation": operation}):
                    raise DiscountSyncError("Faltan permisos para sincronizar descuentos; no uses el rol web.")
        if not connection.scalar(text("""
            SELECT pg_catalog.has_sequence_privilege(current_user,
                pg_catalog.pg_get_serial_sequence('public."Discounts"', 'discount_id'), 'USAGE')
        """)):
            raise DiscountSyncError("Falta USAGE en la secuencia de descuentos.")
        count = connection.scalar(text('SELECT count(*) FROM public."Discounts"'))
    return dict(status="preflight_ok", revision=DISCOUNT_SCHEMA_REVISION,
                database=expected_database, discount_count=count, writes=False, loyverse_contacted=False)


async def execute(service, token: SecretStr) -> int:
    async with LoyverseAPIClient(access_token=token) as client:
        return await service.synchronize(client)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-host", help="Obligatorio en cloud; endpoint directo autorizado.")
    parser.add_argument("--store-id", required=True, help="UUID que debe coincidir con LOYVERSE_STORE_ID.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    engine = None
    try:
        store_id = str(UUID(args.store_id))
        if store_id != str(UUID(os.getenv("LOYVERSE_STORE_ID", ""))):
            raise DiscountSyncError("--store-id debe coincidir con LOYVERSE_STORE_ID.")
        target = None
        if environment_mode() == "cloud":
            target = CloudDatabaseTarget(args.expected_database, args.expected_host or "")
            if (os.getenv("EXPECTED_DATABASE") != target.expected_database
                    or os.getenv("EXPECTED_DATABASE_HOST") != target.expected_host):
                raise DiscountSyncError("Los argumentos deben coincidir con el destino cloud del entorno.")
            if "-pooler." in target.expected_host:
                raise DiscountSyncError("La operación requiere un endpoint directo, sin pooler.")
        elif args.expected_host:
            raise DiscountSyncError("--expected-host solo corresponde al modo cloud.")
        # Validar también base y tienda antes de abrir una conexión.
        service = DiscountSyncService(lambda: None, expected_database=args.expected_database,
                                      store_id=store_id, cloud_target=target)
        raw_url = os.getenv("DATABASE_URL", "")
        url = target.validate_url(raw_url) if target else local_database_url(raw_url)
        token = None
        if args.execute:
            raw_token = os.getenv("LOYVERSE_ACCESS_TOKEN", "")
            if not raw_token or any(c.isspace() for c in raw_token):
                raise DiscountSyncError("--execute requiere LOYVERSE_ACCESS_TOKEN válido en el entorno.")
            token = SecretStr(raw_token)
        engine = create_database_engine(url, target=target)
        report = preflight(engine, expected_database=args.expected_database, cloud_target=target)
        if args.execute:
            service.session_factory = sessionmaker(bind=engine)
            processed = asyncio.run(execute(service, token))
            report.update(status="synchronized", processed=processed, store_id=store_id,
                          writes=True, loyverse_contacted=True)
        print(json.dumps(report, ensure_ascii=True))
        return 0
    except (DatabaseTargetError, DiscountSyncError, DiscountValidationError, LoyverseAPIError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=True))
        return 1
    except Exception:
        print(json.dumps({"status": "error", "message": "No se pudo completar la operación; revisa configuración y estado antes de repetir."}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

"""Sincronización manual: preflight por defecto; --execute autoriza la escritura.

No importa FastAPI, la conexión global ni .env. Credenciales sólo por entorno.
"""

import argparse
import asyncio
import json
import os

from pydantic import SecretStr
from sqlalchemy import MetaData, func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.connection import (
    CloudDatabaseTarget, DatabaseTargetError, create_database_engine, environment_mode,
)
from app.models import catalog_metadata
from app.services.catalog_errors import CatalogSyncError
from app.services.catalog_manifest import CatalogPlanChanged, catalog_manifest, manifest_sha256
from app.services.catalog_probe import fetch_catalog
from app.services.catalog_transform import transform_catalog
from app.services.loyverse_client import LoyverseAPIClient, LoyverseAPIError
from app.services.sync_service import CATALOG_SCHEMA_REVISION, CATALOG_COMPATIBLE_REVISIONS, SyncService, validate_catalog_revision


WRITE_TABLES = {
    "Products": "product_id", "ProductVariants": "product_variant_id",
    "ProductModifiers": "product_modifier_id", "ProductComplements": "product_complement_id",
}


def _table_counts(connection) -> dict:
    return {name: connection.scalar(select(func.count()).select_from(table.to_metadata(
        MetaData(), schema="public",
    ))) for name, table in catalog_metadata.tables.items()}


def _inspect_operator(connection) -> dict:
    """Comprueba capacidad, sin probar DML ni consumir secuencias."""
    user, session_user, default_read_only, recovery = connection.execute(text(
        "SELECT current_user, session_user, current_setting('default_transaction_read_only'), "
        "pg_catalog.pg_is_in_recovery()"
    )).one()
    driver = connection.connection.driver_connection
    if (user != session_user or user != connection.engine.url.username
            or user == "mitbol_render_reader"):
        raise DatabaseTargetError("La sincronización requiere la identidad operativa de la conexión, no el lector.")
    if default_read_only != "off" or driver.readonly is True or recovery:
        raise DatabaseTargetError("La conexión operativa hereda solo lectura o apunta a una réplica.")
    if not connection.scalar(text(
        "SELECT pg_catalog.has_database_privilege(current_user, current_database(), 'CONNECT') "
        "AND pg_catalog.has_schema_privilege(current_user, 'public', 'USAGE')"
    )):
        raise DatabaseTargetError("Falta CONNECT o USAGE para el catálogo autorizado.")
    for name in (*catalog_metadata.tables, "alembic_version"):
        relation = f'public."{name}"'
        valid = connection.scalar(text(
            "SELECT c.relkind = 'r' AND NOT c.relrowsecurity "
            "AND c.oid = pg_catalog.to_regclass(:unqualified) "
            "AND pg_catalog.has_table_privilege(current_user, c.oid, 'SELECT') "
            "FROM pg_catalog.pg_class c WHERE c.oid = pg_catalog.to_regclass(:relation)"
        ), {"relation": relation, "unqualified": f'"{name}"'})
        if not valid:
            raise DatabaseTargetError("Falta una tabla legible en public, hay RLS o search_path desvía el catálogo.")
        privileges = ("INSERT", "UPDATE") if name in WRITE_TABLES else (
            ("INSERT", "DELETE") if name == "ProductModifierAssignments" else ()
        )
        for privilege in privileges:
            if not connection.scalar(text(
                "SELECT pg_catalog.has_table_privilege(current_user, :relation, :privilege)"
            ), {"relation": relation, "privilege": privilege}):
                raise DatabaseTargetError("Faltan permisos para las operaciones comerciales previstas.")
        if name in WRITE_TABLES:
            if not connection.scalar(text(
                "SELECT pg_catalog.has_sequence_privilege(current_user, "
                "pg_catalog.pg_get_serial_sequence(:relation, :column), 'USAGE,UPDATE')"
            ), {"relation": relation, "column": WRITE_TABLES[name]}):
                raise DatabaseTargetError("Falta acceso a una secuencia comercial; no se consumió ningún valor.")
    return {"user": user, "default_transaction_read_only": default_read_only,
            "write_permissions_verified": True}


def preflight(engine, target: CloudDatabaseTarget, *, require_empty: bool = False) -> dict:
    # Sólo inspección; READ ONLY refuerza la protección frente a DML accidental.
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        target.validate_connection(connection)
        operator = _inspect_operator(connection)
        revision = validate_catalog_revision(connection)
        counts = _table_counts(connection)
        if require_empty and any(counts.values()):
            raise DatabaseTargetError("La preparación inicial requiere las ocho tablas vacías; no se autorizan escrituras.")
        info = connection.connection.driver_connection.info
        tls = {"protocol": info.ssl_attribute("protocol"),
               "sslmode": info.dsn_parameters.get("sslmode"),
               "channel_binding": info.dsn_parameters.get("channel_binding")}
    return {"status": "preflight_ok", "database": target.expected_database,
            "host": target.expected_host, "revision": revision,
            **operator, "tls": tls, "table_counts": counts,
            "writes": False, "loyverse_contacted": False}


async def preview_catalog(store_id: str, token: SecretStr) -> dict:
    """Misma lectura y transformación de HU-008, sin servicio de persistencia."""
    async with LoyverseAPIClient(access_token=token, max_attempts=1) as client:
        snapshot = await fetch_catalog(client, store_id, include_deleted=True)
        plan = transform_catalog(snapshot)
        if not plan.products or not plan.variants:
            raise DatabaseTargetError("El catálogo inicial no contiene productos y variantes importables; revisa la tienda.")
        manifest = catalog_manifest(plan, snapshot.store)
        return {
            "catalog_manifest": manifest, "catalog_sha256": manifest_sha256(manifest),
            "store": snapshot.store, "snapshot_at": plan.synced_at.isoformat(),
            "http_requests": client.request_count, "pages_received": dict(client.page_counts),
            "planned_initial_inserts": {
                "Products": len(plan.products), "ProductVariants": len(plan.variants),
                "ProductModifiers": len(plan.modifiers), "ProductComplements": len(plan.complements),
                "ProductModifierAssignments": len(plan.assignments),
            },
            "commercially_available_variants": sum(v.is_commercially_available for v in plan.variants),
            "commercially_unavailable_variants": sum(not v.is_commercially_available for v in plan.variants),
            "tombstones": {"products": len(plan.deleted_products), "variants": len(plan.deleted_variants),
                           "modifiers": len(plan.deleted_modifiers), "complements": len(plan.deleted_complements)},
            "loyverse_contacted": True,
        }


def _approved_preflight(target: CloudDatabaseTarget, user: str, store_id: str) -> dict:
    try:
        report = json.loads(os.environ["MITBOL_SYNC_APPROVED_PREFLIGHT"])
        valid = (
            report["status"] == "ready_for_initial_sync" and report["writes"] is False
            and report["database"] == target.expected_database and report["host"] == target.expected_host
            and report["user"] == user and report["revision"] in CATALOG_COMPATIBLE_REVISIONS
            and report["catalog_manifest"]["store"]["id"] == store_id
            and report["catalog_sha256"] == manifest_sha256(report["catalog_manifest"])
            and set(report["table_counts"]) == set(catalog_metadata.tables)
            and all(value == 0 for value in report["table_counts"].values())
        )
        if not valid:
            raise ValueError
        return report
    except (KeyError, TypeError, ValueError):
        raise DatabaseTargetError("--execute requiere MITBOL_SYNC_APPROVED_PREFLIGHT válido del preflight completo revisado.") from None


def _initial_write_guard(target, approved):
    def validate(session, snapshot, plan):
        actual = catalog_manifest(plan, snapshot.store)
        if actual != approved["catalog_manifest"]:
            raise CatalogPlanChanged(approved["catalog_manifest"], actual)
        connection = session.connection()
        target.validate_connection(connection)
        if validate_catalog_revision(connection) != approved["revision"]:
            raise DatabaseTargetError("La revisión cambió desde el preflight aprobado.")
        operator = _inspect_operator(connection)
        if operator["user"] != approved["user"]:
            raise DatabaseTargetError("La identidad operativa cambió desde el preflight.")
        if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "off":
            raise DatabaseTargetError("La transacción operativa debe permitir las escrituras autorizadas.")
        if connection.exec_driver_sql("SHOW transaction_isolation").scalar_one() != "read committed":
            raise DatabaseTargetError("La carga inicial requiere READ COMMITTED para verificar el vaciado tras los locks.")
        # Evita otro DML entre el último vaciado y nuestro commit; SELECT sigue permitido.
        tables = ", ".join(f'public."{name}"' for name in catalog_metadata.tables)
        connection.exec_driver_sql(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE NOWAIT")
        if any(_table_counts(connection).values()):
            raise DatabaseTargetError("El catálogo dejó de estar vacío; no se ejecutó DML.")
    return validate


async def synchronize(engine, target: CloudDatabaseTarget, store_id: str, token: SecretStr,
                      approved: dict | None = None) -> dict:
    # SyncService vuelve a comprobar destino y revisión dentro de su única
    # transacción, antes del bloqueo y de cualquier llamada HTTP.
    service = SyncService(
        sessionmaker(bind=engine), expected_database=target.expected_database,
        store_id=store_id, cloud_target=target,
        before_write=_initial_write_guard(target, approved) if approved else None,
    )
    async with LoyverseAPIClient(access_token=token, max_attempts=1) as client:
        result = await service.synchronize(client)
    return {"status": "synchronized", "database": target.expected_database,
            "store_id": store_id, "result": result, "writes": True,
            "catalog_sha256": approved["catalog_sha256"] if approved and "catalog_sha256" in approved else None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--store-id", help="ID exacto autorizado. En preflight valida y previsualiza el catálogo inicial sin escribir.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="Sólo inspección; es el modo predeterminado.")
    mode.add_argument("--execute", action="store_true", help="Autoriza una sincronización comercial real.")
    arguments = parser.parse_args(argv)
    engine = None
    try:
        if environment_mode() != "cloud":
            raise DatabaseTargetError("El comando requiere APP_ENV=cloud explícito y no carga .env.")
        if os.getenv("ADMIN_ENABLED", "").strip().lower() != "false":
            raise DatabaseTargetError("El comando requiere ADMIN_ENABLED=false explícito.")
        target = CloudDatabaseTarget(arguments.expected_database, arguments.expected_host)
        if (os.getenv("EXPECTED_DATABASE") != target.expected_database
                or os.getenv("EXPECTED_DATABASE_HOST") != target.expected_host):
            raise DatabaseTargetError("Los argumentos deben coincidir con EXPECTED_DATABASE y EXPECTED_DATABASE_HOST.")
        value = os.getenv("DATABASE_URL")
        if not value:
            raise DatabaseTargetError("Configura DATABASE_URL explícita para la operación manual.")
        url = target.validate_url(value)
        if url.username == "mitbol_render_reader":
            raise DatabaseTargetError("Utiliza el usuario operativo; mitbol_render_reader no puede sincronizar.")
        if "-pooler." in target.expected_host:
            raise DatabaseTargetError("Las operaciones manuales requieren el endpoint directo, sin -pooler.")
        token = None
        if arguments.execute or arguments.store_id is not None:
            raw_token = os.getenv("LOYVERSE_ACCESS_TOKEN", "").strip()
            if not raw_token or any(character.isspace() for character in raw_token):
                raise DatabaseTargetError("La sincronización requiere un LOYVERSE_ACCESS_TOKEN válido y no vacío.")
            token = SecretStr(raw_token)
            if (not arguments.store_id or arguments.store_id != arguments.store_id.strip()
                    or arguments.store_id != os.getenv("LOYVERSE_STORE_ID")):
                raise DatabaseTargetError("--store-id debe coincidir con LOYVERSE_STORE_ID explícito.")
        approved = _approved_preflight(target, url.username, arguments.store_id) if arguments.execute else None
        engine = create_database_engine(url, target=target)
        report = preflight(engine, target, require_empty=bool(arguments.store_id))
        if arguments.execute:
            report = asyncio.run(synchronize(engine, target, arguments.store_id, token, approved))
        elif arguments.store_id:
            report.update(asyncio.run(preview_catalog(arguments.store_id, token)))
            # Repite destino/permisos/vaciado tras HTTP; sigue sin ejecutar DML.
            after = preflight(engine, target, require_empty=True)
            report.update(table_counts=after["table_counts"], status="ready_for_initial_sync")
        report.update(app_env="cloud", admin_enabled=False)
        print(json.dumps(report, ensure_ascii=True))
        return 0
    except CatalogPlanChanged as error:
        print(json.dumps({"status": "catalog_changed", "message": str(error),
                          "differences": error.differences, "writes": False}, ensure_ascii=True))
        return 1
    except (DatabaseTargetError, CatalogSyncError, LoyverseAPIError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=True))
        return 1
    except Exception:
        # Incluye errores del controlador y fallos inesperados, sin traceback/URL.
        print(json.dumps({"status": "error", "message": "Falló la operación manual; no se muestran secretos ni SQL."}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

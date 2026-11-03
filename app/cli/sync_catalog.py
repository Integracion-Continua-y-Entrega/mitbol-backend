"""Sincronización manual: preflight por defecto; --execute autoriza la escritura.

No importa FastAPI, la conexión global ni .env. Credenciales sólo por entorno.
"""

import argparse
import asyncio
import json
import os

from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.db.connection import (
    CloudDatabaseTarget, DatabaseTargetError, create_database_engine, environment_mode,
)
from app.services.catalog_errors import CatalogSyncError
from app.services.loyverse_client import LoyverseAPIClient, LoyverseAPIError
from app.services.sync_service import CATALOG_SCHEMA_REVISION, SyncService, validate_catalog_revision


def preflight(engine, target: CloudDatabaseTarget) -> dict:
    # READ ONLY garantiza que incluso esta verificación no puede modificar datos.
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        target.validate_connection(connection)
        validate_catalog_revision(connection)
    return {"status": "preflight_ok", "database": target.expected_database,
            "host": target.expected_host, "revision": CATALOG_SCHEMA_REVISION,
            "writes": False, "loyverse_contacted": False}


async def synchronize(engine, target: CloudDatabaseTarget, store_id: str, token: SecretStr) -> dict:
    # SyncService vuelve a comprobar destino y revisión dentro de su única
    # transacción, antes del bloqueo y de cualquier llamada HTTP.
    service = SyncService(
        sessionmaker(bind=engine), expected_database=target.expected_database,
        store_id=store_id, cloud_target=target,
    )
    async with LoyverseAPIClient(access_token=token) as client:
        result = await service.synchronize(client)
    return {"status": "synchronized", "database": target.expected_database,
            "store_id": store_id, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--store-id", help="ID exacto de la tienda autorizada; obligatorio con --execute.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="Sólo inspección; es el modo predeterminado.")
    mode.add_argument("--execute", action="store_true", help="Autoriza una sincronización comercial real.")
    arguments = parser.parse_args(argv)
    engine = None
    try:
        if environment_mode() != "cloud":
            raise DatabaseTargetError("El comando requiere APP_ENV=cloud explícito y no carga .env.")
        target = CloudDatabaseTarget(arguments.expected_database, arguments.expected_host)
        if (os.getenv("EXPECTED_DATABASE") != target.expected_database
                or os.getenv("EXPECTED_DATABASE_HOST") != target.expected_host):
            raise DatabaseTargetError("Los argumentos deben coincidir con EXPECTED_DATABASE y EXPECTED_DATABASE_HOST.")
        value = os.getenv("DATABASE_URL")
        if not value:
            raise DatabaseTargetError("Configura DATABASE_URL explícita para la operación manual.")
        url = target.validate_url(value)
        if "-pooler." in target.expected_host:
            raise DatabaseTargetError("Las operaciones manuales requieren el endpoint directo, sin -pooler.")
        token = None
        if arguments.execute:
            raw_token = os.getenv("LOYVERSE_ACCESS_TOKEN", "").strip()
            if not raw_token or any(character.isspace() for character in raw_token):
                raise DatabaseTargetError("La sincronización requiere un LOYVERSE_ACCESS_TOKEN válido y no vacío.")
            token = SecretStr(raw_token)
            if (not arguments.store_id or arguments.store_id != arguments.store_id.strip()
                    or arguments.store_id != os.getenv("LOYVERSE_STORE_ID")):
                raise DatabaseTargetError("--store-id debe coincidir con LOYVERSE_STORE_ID explícito.")
        engine = create_database_engine(url, target=target)
        report = preflight(engine, target)
        if arguments.execute:
            report = asyncio.run(synchronize(engine, target, arguments.store_id, token))
        print(json.dumps(report, ensure_ascii=True))
        return 0
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

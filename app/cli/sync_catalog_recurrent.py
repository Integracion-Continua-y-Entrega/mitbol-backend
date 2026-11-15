"""HU-008 recurrente: simulación por defecto; --execute exige aprobación propia.

No lee .env, importa FastAPI ni ejecuta migraciones. Funciona en local/test o
cloud con destino explícito. La carga inicial conserva su comando y guardas.
"""

import argparse
import asyncio
import json
import os
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.db.connection import (
    CloudDatabaseTarget, DatabaseTargetError, create_database_engine, environment_mode,
    local_database_url,
)
from app.services.catalog_errors import (
    CatalogPersistenceError, CatalogSyncBusyError, CatalogTargetError, CatalogValidationError,
)
from app.services.loyverse_client import (
    LoyverseAPIClient, LoyverseAPIError, LoyverseConfigurationError, LoyverseResponseError, LoyverseTransientError,
)
from app.services.recurring_catalog_sync import RecurringCatalogSync


def configuration(arguments):
    if not os.getenv("APP_ENV"):
        raise CatalogTargetError("Configura APP_ENV explícitamente; no se carga .env.")
    mode = environment_mode()
    if os.getenv("ADMIN_ENABLED", "").strip().lower() != "false":
        raise CatalogTargetError("El ejecutor exige ADMIN_ENABLED=false explícito.")
    if (os.getenv("EXPECTED_DATABASE") != arguments.expected_database
            or os.getenv("EXPECTED_DATABASE_HOST") != arguments.expected_host):
        raise CatalogTargetError("Los argumentos deben coincidir con el destino autorizado en el proceso.")
    if not arguments.store_id or arguments.store_id != os.getenv("LOYVERSE_STORE_ID"):
        raise CatalogTargetError("--store-id debe coincidir con LOYVERSE_STORE_ID explícito.")
    value = os.getenv("DATABASE_URL")
    if not value:
        raise CatalogTargetError("DATABASE_URL de proceso es obligatoria.")
    target = CloudDatabaseTarget(arguments.expected_database, arguments.expected_host) if mode == "cloud" else None
    url = target.validate_url(value) if target else local_database_url(value)
    if url.host != arguments.expected_host or url.database != arguments.expected_database:
        raise CatalogTargetError("La URL no coincide con el host y base autorizados.")
    if not url.username or url.username in {"mitbol_render_reader", "mitbol_admin_writer"}:
        raise CatalogTargetError("Utiliza una identidad operativa comercial, no los roles web.")
    if target and "-pooler." in arguments.expected_host:
        raise CatalogTargetError("La operación recurrente cloud requiere el endpoint directo.")
    if url.drivername not in {"postgresql", "postgresql+psycopg2"}:
        raise CatalogTargetError("La operación requiere PostgreSQL mediante psycopg2.")
    token = os.getenv("LOYVERSE_ACCESS_TOKEN", "")
    if not token or any(char.isspace() for char in token):
        raise CatalogTargetError("Se requiere LOYVERSE_ACCESS_TOKEN válido, incluso para descargar una simulación.")
    approval = None
    if arguments.execute:
        try:
            approval = json.loads(os.environ["MITBOL_RECURRENT_SYNC_APPROVAL"])
            if not isinstance(approval, dict) or approval.get("approved") is not True:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise CatalogTargetError("--execute requiere MITBOL_RECURRENT_SYNC_APPROVAL revisado; la aprobación inicial no lo sustituye.") from None
    return url.set(drivername="postgresql+psycopg2"), target, SecretStr(token), approval


async def execute(engine, target, arguments, token, approval):
    runner = RecurringCatalogSync(sessionmaker(bind=engine),
        expected_database=arguments.expected_database, expected_host=arguments.expected_host,
        store_id=arguments.store_id, cloud_target=target)
    async with LoyverseAPIClient(access_token=token) as client:
        return await runner.run(client, dry_run=not arguments.execute, approval=approval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--store-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Descarga, valida y compara sin escribir ni consumir secuencias; predeterminado.")
    mode.add_argument("--execute", action="store_true", help="Actualiza solo con aprobación recurrente del destino/tienda/revisión.")
    arguments = parser.parse_args(argv)
    engine = None
    try:
        url, target, token, approval = configuration(arguments)
        engine = create_database_engine(url, target=target)
        report = asyncio.run(execute(engine, target, arguments, token, approval))
        print(json.dumps(report, ensure_ascii=True))
        return 0
    except (DatabaseTargetError, CatalogTargetError, CatalogValidationError, LoyverseConfigurationError, LoyverseResponseError):
        code, category, message = 2, "validation", "Configuración, autorización o catálogo inválido; no se aplicaron cambios."
    except CatalogSyncBusyError:
        code, category, message = 3, "busy", "Ya hay una operación comercial en curso; reprograma la ejecución."
    except LoyverseAPIError as error:
        code = 4
        temporary = (isinstance(error, LoyverseTransientError)
                     or error.status_code in {408, 429}
                     or (error.status_code is not None and 500 <= error.status_code < 600))
        category = "provider_temporary" if temporary else "provider_rejected"
        message = "Falló la lectura de Loyverse; se conservan los datos previos."
    except CatalogPersistenceError:
        code, category, message = 5, "database", "Falló PostgreSQL; verifica el estado real antes de repetir una ejecución."
    except Exception:
        code, category, message = 6, "unexpected", "Falló la ejecución; revisa el estado sin publicar secretos ni trazas."
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps({"status": "error", "run_id": uuid4().hex, "code": category,
                      "exit_code": code, "message": message,
                      "writes": None if arguments.execute and code in {5, 6} else False,
                      "verification_required": arguments.execute and code in {5, 6},
                      "errors": [category]}, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

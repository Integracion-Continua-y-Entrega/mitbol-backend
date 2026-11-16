"""Perfil mínimo del trabajador cloud, compartido con el verificador SQL."""

from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.db.connection import DatabaseTargetError
from app.db.schema_compatibility import validate_bridge_schema


def worker_policy_sql():
    try:
        source = (Path(__file__).resolve().parents[2] /
                  "docs/database/verify_catalog_worker.sql").read_text(encoding="utf-8")
        start = source.index("DO $policy$")
        end = source.index("$policy$;", start) + len("$policy$;")
        return source[start:end]
    except (OSError, ValueError):
        raise DatabaseTargetError("No está disponible el perfil mínimo del trabajador.") from None


def validate_worker_connection(connection, target):
    # La marca sólo se fija después de verificar host/base, driver y TLS efectivo.
    # SET LOCAL no altera configuraciones persistentes del servidor.
    target.validate_connection(connection)
    try:
        validate_bridge_schema(connection)
        connection.exec_driver_sql("SET LOCAL mitbol.external_tls_verified='verify-full-channel-binding'")
        connection.exec_driver_sql(worker_policy_sql(), execution_options={"no_parameters": True})
    except (SQLAlchemyError, DatabaseTargetError):
        raise DatabaseTargetError("El trabajador no cumple el perfil mínimo de permisos revisado.") from None

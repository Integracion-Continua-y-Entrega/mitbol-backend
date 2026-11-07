"""Reutiliza el verificador aprobado, sin grants ni acceso a datos de aplicación."""

from pathlib import Path

from app.db.connection import DatabaseTargetError


def writer_policy_sql():
    # El despliegue desde el repositorio incluye docs/database. Una ausencia o
    # archivo inesperado falla cerrado; no hay una segunda matriz de permisos.
    try:
        source = (Path(__file__).resolve().parents[2] / "docs/database/verify_admin_writer_neon.sql").read_text(encoding="utf-8")
        start = source.index("DO $policy$")
        end = source.index("$policy$;", start) + len("$policy$;")
        return source[start:end]
    except (OSError, ValueError):
        raise DatabaseTargetError("No está disponible el perfil SQL aprobado del escritor.") from None


def validate_writer_connection(driver_connection):
    # Llamar sólo después de validar TLS/destino con CloudDatabaseTarget.
    with driver_connection.cursor() as cursor:
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cursor.execute("SET LOCAL search_path = pg_catalog, public")
        cursor.execute("SET LOCAL statement_timeout = '15s'")
        cursor.execute("SET LOCAL mitbol.external_tls_verified = 'verify-full-channel-binding'")
        cursor.execute(writer_policy_sql())

"""Verificación de Neon antes de migrar: sólo lectura, sin .env ni Loyverse."""

import argparse
import json
import os
import re
import socket
import sys

from alembic.runtime.migration import MigrationContext
import psycopg2
import sqlalchemy
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.db.connection import (
    DatabaseTargetError, cloud_target_from_environment, create_database_engine,
)


APPLICATION_OBJECTS_SQL = """
SELECT n.nspname, c.relname, c.relkind
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND NOT (n.nspname = 'public' AND c.relname = 'alembic_version')
ORDER BY n.nspname, c.relname
"""


def runtime_versions() -> dict:
    return {"python": ".".join(map(str, sys.version_info[:3])),
            "psycopg2": psycopg2.__version__.split()[0],
            "libpq": psycopg2.__libpq_version__, "sqlalchemy": sqlalchemy.__version__}


def connection_diagnostic(error: Exception, stage: str) -> dict:
    """Clasifica en memoria; nunca devuelve mensajes, SQL, DSN, args ni traceback."""
    original = error.orig if isinstance(error, DBAPIError) else error
    sqlstate = getattr(original, "pgcode", None)
    if not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        sqlstate = None
    # libpq no suele proporcionar SQLSTATE para errores durante el handshake.
    # Sólo se comparan frases conocidas; el texto completo jamás se publica.
    message = str(original).lower()
    if isinstance(original, UnicodeError):
        category, code = "client", "CLIENT_ENCODING_ERROR"
    elif isinstance(original, DatabaseTargetError):
        category, code = "validation", "TARGET_VALIDATION_FAILED"
        if original.code in {
            "EFFECTIVE_HOST_MISMATCH", "EFFECTIVE_DATABASE_MISMATCH", "EFFECTIVE_PORT_MISMATCH",
            "EFFECTIVE_USER_MISMATCH", "EFFECTIVE_OPTIONS_REJECTED", "TLS_NOT_ACTIVE",
            "TLS_MODE_MISMATCH", "CHANNEL_BINDING_MISMATCH", "TLS_CA_MISMATCH",
        }:
            code = original.code
    elif isinstance(original, socket.gaierror) or any(phrase in message for phrase in (
        "could not translate host name", "getaddrinfo failed", "name or service not known",
        "no such host is known", "could not resolve hostname",
    )):
        category, code = "dns", "DNS_RESOLUTION_FAILED"
    elif "invalid connection option" in message or "invalid sslmode value" in message:
        category, code = "client", "UNSUPPORTED_CONNECTION_OPTION"
    elif "channel binding" in message:
        category, code = "tls", "CHANNEL_BINDING_FAILED"
    elif "does not match host name" in message or "hostname mismatch" in message:
        category, code = "tls", "TLS_HOSTNAME_MISMATCH"
    elif any(phrase in message for phrase in (
        "certificate verify failed", "certificate verification failed", "root certificate file",
        "unable to get local issuer certificate", "self-signed certificate",
    )):
        category, code = "tls", "TLS_CERTIFICATE_VERIFICATION_FAILED"
    elif sqlstate in {"28P01", "28000"} or any(phrase in message for phrase in (
        "password authentication failed", "authentication failed", "no password supplied",
    )):
        category, code = "authentication", "AUTHENTICATION_FAILED"
    elif sqlstate == "3D000" or ("database" in message and "does not exist" in message):
        category, code = "postgresql", "DATABASE_NOT_FOUND"
    elif sqlstate == "42501":
        category, code = "postgresql", "INSUFFICIENT_PRIVILEGE"
    elif sqlstate == "25006":
        category, code = "postgresql", "READ_ONLY_VIOLATION"
    elif sqlstate:
        category, code = "postgresql", "POSTGRESQL_QUERY_FAILED"
    elif isinstance(original, TimeoutError) or "timeout expired" in message or "timed out" in message:
        category, code = "network", "CONNECTION_TIMEOUT"
    elif isinstance(original, ConnectionRefusedError) or "connection refused" in message:
        category, code = "network", "CONNECTION_REFUSED"
    elif "ssl syscall" in message or "closed unexpectedly" in message or "server closed the connection" in message:
        category, code = "connection", "CONNECTION_CLOSED"
    elif any(phrase in message for phrase in ("ssl error", "tls error", "server does not support ssl")):
        category, code = "tls", "TLS_NEGOTIATION_FAILED"
    elif stage in {"dns", "tcp"} and isinstance(original, OSError):
        category, code = "network", "NETWORK_UNREACHABLE"
    elif isinstance(original, (psycopg2.OperationalError, psycopg2.InterfaceError)):
        category, code = "connection", "POSTGRESQL_CONNECTION_FAILED"
    else:
        category, code = "client", "UNEXPECTED_CLIENT_ERROR"
    # Nombres de clase cerrados: ni tipos personalizados ni mensajes se reflejan.
    error_type = next((name for cls, name in (
        (DatabaseTargetError, "DatabaseTargetError"), (UnicodeError, "UnicodeError"),
        (socket.gaierror, "gaierror"), (TimeoutError, "TimeoutError"),
        (psycopg2.OperationalError, "OperationalError"), (psycopg2.InterfaceError, "InterfaceError"),
        (psycopg2.ProgrammingError, "ProgrammingError"), (psycopg2.Error, "DatabaseError"),
        (OSError, "OSError"), (SQLAlchemyError, "SQLAlchemyError"),
        (TypeError, "TypeError"), (AttributeError, "AttributeError"), (KeyError, "KeyError"),
        (ValueError, "ValueError"), (NotImplementedError, "NotImplementedError"), (RuntimeError, "RuntimeError"),
    ) if isinstance(original, cls)), "UnexpectedError")
    return {"stage": stage, "category": category, "code": code,
            "error_type": error_type, "sqlstate": sqlstate}


def inspect_database(engine, target, expected_user: str, *, diagnostics: dict | None = None) -> dict:
    diagnostics = diagnostics if diagnostics is not None else {"checks_passed": []}
    diagnostics["stage"] = "postgresql_connect"
    # readonly aquí es una propiedad de la transacción, no del rol. Esto admite
    # neondb_owner para inspección sin usar la configuración del lector de Render.
    # Separar connect de execution_options garantiza el cierre si falla readonly.
    with engine.connect() as connection:
        try:
            diagnostics["checks_passed"].append("postgresql_connect")
            diagnostics["stage"] = "readonly_setup"
            connection.execution_options(postgresql_readonly=True)
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
            diagnostics["checks_passed"].append("readonly_setup")
            diagnostics["stage"] = "target_validation"
            target.validate_connection(connection)
            diagnostics["checks_passed"].append("target_validation")
            diagnostics["stage"] = "identity_query"
            identity = connection.exec_driver_sql(
                "SELECT pg_catalog.current_database(), current_user, session_user, "
                "pg_catalog.current_setting('transaction_read_only')"
            ).one()
            if (identity[0] != target.expected_database or identity[1] != expected_user
                    or identity[2] != expected_user or identity[3] != "on"):
                raise DatabaseTargetError("La base, usuario o transacción de lectura no coincide con lo autorizado.")
            diagnostics["checks_passed"].append("identity_query")
            diagnostics["stage"] = "tls_validation"
            info = connection.connection.driver_connection.info
            protocol = info.ssl_attribute("protocol")
            if protocol not in {"TLSv1.2", "TLSv1.3"}:
                raise DatabaseTargetError("La conexión no acredita una versión TLS admitida.")
            diagnostics["checks_passed"].append("tls_validation")
            diagnostics["stage"] = "revision_check"
            heads = MigrationContext.configure(connection).get_current_heads()
            if heads:
                raise DatabaseTargetError("La base ya tiene revisiones Alembic; detener la preparación inicial.")
            diagnostics["checks_passed"].append("revision_check")
            diagnostics["stage"] = "schema_check"
            objects = connection.exec_driver_sql(APPLICATION_OBJECTS_SQL).all()
            if objects:
                raise DatabaseTargetError("La base contiene objetos de aplicación; no está vacía para la migración inicial.")
            diagnostics["checks_passed"].append("schema_check")
            return {
                "status": "ready_for_migrations", "host": info.host, "port": int(info.port),
                "database": identity[0], "user": identity[1], "session_user": identity[2],
                "tls_active": bool(info.ssl_in_use), "tls_protocol": protocol,
                "sslmode": info.dsn_parameters["sslmode"],
                "channel_binding": info.dsn_parameters["channel_binding"],
                "read_only": True, "alembic_heads": [], "application_objects": [], "writes": False,
            }
        finally:
            if sys.exc_info()[0] is None:
                diagnostics["stage"] = "rollback"
                connection.rollback()
                diagnostics["checks_passed"].append("rollback")
            else:
                # Conservar el error original si también falla la limpieza.
                try:
                    connection.rollback()
                except Exception:
                    diagnostics["rollback_failed"] = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-url", action="store_true", help="Valida configuración sin abrir conexiones.")
    parser.add_argument("--diagnose", action="store_true", help="Añade pruebas DNS/TCP y diagnóstico sanitizado.")
    parser.add_argument("--expected-user", default="neondb_owner", help="Usuario y session_user autorizados.")
    arguments = parser.parse_args(argv)
    engine = None
    diagnostics = {"stage": "configuration", "checks_passed": []}
    report = None
    exit_code = 1
    try:
        target = cloud_target_from_environment()
        if target is None:
            raise DatabaseTargetError("La verificación de Neon requiere APP_ENV=cloud explícito.")
        value = os.getenv("DATABASE_URL")
        if not value:
            raise DatabaseTargetError("Configura DATABASE_URL en el proceso; este comando no carga .env.")
        url = target.validate_url(value)
        if "-pooler." in target.expected_host:
            raise DatabaseTargetError("La verificación inicial requiere el endpoint directo, sin -pooler.")
        if not arguments.expected_user or url.username != arguments.expected_user:
            raise DatabaseTargetError("El usuario de DATABASE_URL no coincide con --expected-user.")
        # La construcción valida CA y overrides libpq, pero no abre sockets.
        engine = create_database_engine(url, target=target, read_only=False)
        diagnostics["checks_passed"].append("configuration")
        if arguments.validate_url:
            report = {
                "status": "configuration_ok", "host": target.expected_host,
                "database": target.expected_database, "user": url.username,
                "sslmode": "verify-full", "channel_binding": "require",
                "connection_checked": False, "writes": False,
            }
        else:
            if arguments.diagnose:
                diagnostics["stage"] = "dns"
                socket.getaddrinfo(url.host, url.port, type=socket.SOCK_STREAM)
                diagnostics["checks_passed"].append("dns")
                diagnostics["stage"] = "tcp"
                # Sólo abrir/cerrar TCP: no envía credenciales ni sentencias SQL.
                with socket.create_connection((url.host, url.port), timeout=5):
                    pass
                diagnostics["checks_passed"].append("tcp")
            report = inspect_database(engine, target, arguments.expected_user, diagnostics=diagnostics)
        exit_code = 0
    except Exception as error:
        report = {"status": "error", "message": "La verificación se detuvo; revisar el código y etapa del diagnóstico.",
                  **connection_diagnostic(error, diagnostics["stage"]),
                  "checks_passed": diagnostics["checks_passed"], "writes": False}
        if diagnostics.get("rollback_failed"):
            report["rollback_failed"] = True
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                if exit_code == 0:
                    report = {"status": "error", "stage": "cleanup", "code": "CLEANUP_FAILED", "writes": False}
                    exit_code = 1
    if arguments.diagnose:
        report["runtime"] = runtime_versions()
        if exit_code == 0:
            report["checks_passed"] = diagnostics["checks_passed"]
    print(json.dumps(report, ensure_ascii=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

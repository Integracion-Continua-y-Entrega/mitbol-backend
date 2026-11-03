"""Política de conexión compartida por API, Alembic y operaciones manuales.

No carga .env, no importa Settings y no abre conexiones al importar el módulo.
La identidad remota se comprueba con TLS verify-full, no con la IP del proxy.
"""

from dataclasses import dataclass
from ipaddress import ip_address
import os
from pathlib import Path
import re

import certifi
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class DatabaseTargetError(ValueError):
    """Error operativo seguro: nunca incluye una URL ni datos del controlador."""


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
LIBPQ_OVERRIDES = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")


def environment_mode(value: str | None = None) -> str:
    mode = (value if value is not None else os.getenv("APP_ENV", "local")).strip().lower()
    if mode not in {"local", "test", "cloud"}:
        raise DatabaseTargetError("APP_ENV debe ser local, test o cloud.")
    if os.getenv("RENDER", "").lower() == "true" and mode != "cloud":
        raise DatabaseTargetError("Render requiere APP_ENV=cloud explícito.")
    return mode


def postgresql_url(value: str | URL) -> URL:
    try:
        url = make_url(value)
        if url.get_backend_name() != "postgresql":
            raise DatabaseTargetError("DATABASE_URL debe utilizar PostgreSQL.")
        # Leer port también detecta un puerto mal formado antes de abrir sockets.
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError
        return url
    except (ArgumentError, TypeError, ValueError):
        raise DatabaseTargetError("DATABASE_URL no es una URL PostgreSQL válida.") from None


def local_database_url(value: str | URL) -> URL:
    """Las operaciones remotas nunca pueden eludir APP_ENV=cloud y su autorización."""
    url = postgresql_url(value)
    if (url.host not in LOOPBACK_HOSTS
            or set(url.query) & {"host", "hostaddr", "service", "servicefile", "dbname", "database", "port"}
            or any(os.getenv(key) for key in LIBPQ_OVERRIDES)):
        raise DatabaseTargetError("Las operaciones remotas requieren APP_ENV=cloud y un destino autorizado.")
    return url


def validate_local_connection(connection) -> None:
    url = local_database_url(connection.engine.url)
    info = connection.connection.driver_connection.info
    if info.host not in LOOPBACK_HOSTS or info.dbname != url.database:
        raise DatabaseTargetError("La conexión real no corresponde al destino local o de pruebas.")


@dataclass(frozen=True)
class CloudDatabaseTarget:
    expected_database: str
    expected_host: str

    def __post_init__(self):
        database = self.expected_database
        if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", database)
                or database.lower() in {"mitbol_dev", "postgres", "template0", "template1"}
                or database.lower().startswith("mitbol_test")):
            raise DatabaseTargetError("EXPECTED_DATABASE debe autorizar una base cloud independiente.")
        host = self.expected_host
        labels = host.split(".")
        if (len(labels) < 2 or len(host) > 253 or host != host.lower()
                or host.endswith((".localhost", ".local", ".localdomain", ".test", ".invalid"))
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in labels)):
            raise DatabaseTargetError("EXPECTED_DATABASE_HOST debe ser un hostname remoto explícito.")
        try:
            ip_address(host)
        except ValueError:
            pass
        else:
            raise DatabaseTargetError("El destino cloud requiere un hostname verificable mediante TLS.")

    def validate_url(self, value: str | URL) -> URL:
        url = postgresql_url(value)
        if (url.drivername not in {"postgresql", "postgresql+psycopg2"}
                or url.host != self.expected_host or url.database != self.expected_database
                or not url.username or not url.password):
            raise DatabaseTargetError("DATABASE_URL no coincide con el destino cloud autorizado o falta su credencial.")
        allowed = {"sslmode", "sslrootcert", "channel_binding", "application_name", "connect_timeout"}
        if any(key not in allowed or not isinstance(value, str) for key, value in url.query.items()):
            raise DatabaseTargetError("DATABASE_URL contiene parámetros de conexión no autorizados.")
        if url.query.get("sslmode") != "verify-full":
            raise DatabaseTargetError("Cloud requiere sslmode=verify-full para verificar el servidor TLS.")
        if url.query.get("channel_binding", "require") != "require":
            raise DatabaseTargetError("Cloud requiere channel_binding=require.")
        root = url.query.get("sslrootcert")
        if root and not Path(root).is_file():
            raise DatabaseTargetError("sslrootcert debe señalar un archivo de certificados CA accesible.")
        return url.set(drivername="postgresql+psycopg2", port=url.port or 5432)

    def validate_driver(self, driver_connection, url: URL) -> None:
        """Valida parámetros efectivos, incluidos posibles defaults de libpq."""
        info = driver_connection.info
        params = info.dsn_parameters
        if (info.host != self.expected_host or info.dbname != self.expected_database
                or str(info.port) != str(url.port or 5432) or info.user != url.username
                or not info.ssl_in_use or params.get("sslmode") != "verify-full"
                or params.get("channel_binding") != "require"
                or params.get("hostaddr") or params.get("service") or params.get("options")
                or params.get("sslrootcert") != connection_arguments(url, self)["sslrootcert"]):
            raise DatabaseTargetError("La identidad o TLS de la conexión real no coincide con el destino autorizado.")

    def validate_connection(self, connection) -> None:
        """Sólo lectura; se ejecuta dentro de la transacción del operador."""
        url = self.validate_url(connection.engine.url)
        self.validate_driver(connection.connection.driver_connection, url)
        if connection.exec_driver_sql("SELECT pg_catalog.current_database()").scalar_one() != self.expected_database:
            raise DatabaseTargetError("current_database() no coincide con la base cloud autorizada.")


def cloud_target_from_environment() -> CloudDatabaseTarget | None:
    if environment_mode() != "cloud":
        return None
    database = os.getenv("EXPECTED_DATABASE", "")
    host = os.getenv("EXPECTED_DATABASE_HOST", "")
    if not database or not host:
        raise DatabaseTargetError("Cloud requiere EXPECTED_DATABASE y EXPECTED_DATABASE_HOST explícitos.")
    return CloudDatabaseTarget(database, host)


def connection_arguments(url: URL, target: CloudDatabaseTarget | None = None) -> dict:
    arguments = {"connect_timeout": 10}
    if target is not None:
        if any(os.getenv(key) for key in LIBPQ_OVERRIDES):
            raise DatabaseTargetError("El entorno contiene parámetros libpq que pueden cambiar el destino autorizado.")
        arguments.update(
            sslmode="verify-full", sslrootcert=url.query.get("sslrootcert") or certifi.where(),
            channel_binding="require", gssencmode="disable", ssl_min_protocol_version="TLSv1.2",
        )
    return arguments


# Comprobar permisos reales evita que un owner se publique accidentalmente como lector.
READ_ONLY_ROLE_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = current_user
    AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
) OR pg_catalog.has_schema_privilege(current_user, 'public', 'CREATE')
OR pg_catalog.has_database_privilege(current_user, pg_catalog.current_database(), 'CREATE')
OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
    AND (pg_catalog.has_table_privilege(current_user, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
         OR pg_catalog.has_any_column_privilege(current_user, c.oid, 'INSERT,UPDATE,REFERENCES'))
) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'S'
    AND pg_catalog.has_sequence_privilege(current_user, c.oid, 'USAGE,UPDATE')
)
"""


def create_database_engine(value: str | URL, *, target: CloudDatabaseTarget | None = None,
                           read_only: bool = False):
    url = target.validate_url(value) if target else postgresql_url(value)
    defaults = dict(pool_pre_ping=True, pool_size=3, max_overflow=2, pool_timeout=10,
                    hide_parameters=True, connect_args=connection_arguments(url, target))
    engine = create_engine(url, **defaults)
    if target is not None:
        @event.listens_for(engine, "connect")
        def verify_new_connection(driver_connection, connection_record):
            target.validate_driver(driver_connection, url)
            # Una conexión recién abierta no contiene trabajo de la aplicación.
            try:
                with driver_connection.cursor() as cursor:
                    cursor.execute("SELECT pg_catalog.current_database()")
                    if cursor.fetchone()[0] != target.expected_database:
                        raise DatabaseTargetError("La base real no coincide con el destino cloud autorizado.")
                    if read_only:
                        cursor.execute(READ_ONLY_ROLE_SQL)
                        if cursor.fetchone()[0]:
                            raise DatabaseTargetError("La API cloud requiere un usuario PostgreSQL sin permisos de escritura.")
            finally:
                driver_connection.rollback()
            if read_only:
                driver_connection.set_session(readonly=True)
    return engine

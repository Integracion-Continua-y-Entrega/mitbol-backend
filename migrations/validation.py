"""Validaciones de lectura previas a la primera migración."""

from alembic.runtime.migration import MigrationContext
from alembic.util import CommandError
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.db.connection import CloudDatabaseTarget, validate_local_connection


def validate_migration_target(connection: Connection, target: CloudDatabaseTarget | None = None) -> None:
    """Verifica la conexión antes de que Alembic cree siquiera alembic_version."""
    if connection.dialect.name != "postgresql":
        raise CommandError("Las migraciones de MITBOL requieren PostgreSQL.")
    if target is not None:
        target.validate_connection(connection)
    else:
        validate_local_connection(connection)


def validate_initial_database(connection: Connection) -> None:
    """La primera revisión sólo se aplica a una base sin tablas de aplicación."""
    if MigrationContext.configure(connection).get_current_heads():
        return

    inspector = inspect(connection)
    schemas = [
        schema for schema in inspector.get_schema_names()
        if schema != "information_schema" and not schema.startswith("pg_")
    ]
    objects = []
    for schema in schemas:
        tables = set(inspector.get_table_names(schema=schema))
        if schema == inspector.default_schema_name:
            tables.discard("alembic_version")
        objects.extend(tables)
        objects.extend(inspector.get_view_names(schema=schema))
    if objects:
        raise CommandError(
            "La migración inicial requiere una base nueva y vacía. "
            "Se encontraron objetos de aplicación; no se ejecutó la migración. "
            "Revisa DATABASE_URL. No se eliminan items ni categories automáticamente."
        )

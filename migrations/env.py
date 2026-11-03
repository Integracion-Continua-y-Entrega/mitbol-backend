"""Alembic usa los modelos sin importar FastAPI ni requerir un token Loyverse."""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.util import CommandError
from dotenv import dotenv_values
from sqlalchemy import create_engine, pool
from sqlalchemy.exc import SQLAlchemyError

from app.db.connection import (
    DatabaseTargetError, cloud_target_from_environment, connection_arguments, environment_mode,
    local_database_url, postgresql_url,
)
from app.models import Base
from migrations.validation import validate_initial_database, validate_migration_target

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url():
    # attributes permite generar SQL en pruebas con una URL ficticia, sin secretos.
    target = cloud_target_from_environment()
    value = config.attributes.get("database_url") or os.getenv("DATABASE_URL")
    if not value and environment_mode() == "local":
        project_root = Path(__file__).resolve().parents[1]
        local_values = dotenv_values(project_root / ".env")
        if str(local_values.get("APP_ENV", "local")).strip().lower() == "cloud":
            raise CommandError("APP_ENV=cloud debe configurarse en el proceso, no en .env.")
        value = local_values.get("DATABASE_URL")
    if not value:
        raise CommandError("Configura DATABASE_URL antes de utilizar Alembic.")
    return target.validate_url(value) if target else postgresql_url(value)


def run_migrations_offline() -> None:
    """Emite SQL para revisión sin abrir conexiones ni ejecutarlo."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def migrate_connection(connection) -> None:
    validate_migration_target(connection, cloud_target_from_environment())
    validate_initial_database(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Permite aplicar una revisión sobre la misma conexión y transacción ya
    # verificadas por el ejecutor, sin abrir otra conexión con distinta configuración.
    connection = config.attributes.get("connection")
    if connection is not None:
        migrate_connection(connection)
        return
    url = get_database_url()
    if cloud_target_from_environment() is None:
        url = local_database_url(url)
    engine = create_engine(
        url, poolclass=pool.NullPool, hide_parameters=True,
        connect_args=connection_arguments(url, cloud_target_from_environment()),
    )
    try:
        with engine.begin() as connection:
            migrate_connection(connection)
    finally:
        engine.dispose()


try:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
except DatabaseTargetError as error:
    raise CommandError(str(error)) from None
except SQLAlchemyError:
    raise CommandError("Falló la operación PostgreSQL; no se muestran detalles de conexión ni SQL.") from None

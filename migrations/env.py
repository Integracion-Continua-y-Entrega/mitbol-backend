"""Alembic usa los modelos sin importar FastAPI ni requerir un token Loyverse."""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.util import CommandError
from dotenv import dotenv_values
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from app.models import Base
from migrations.validation import validate_initial_database

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_database_url():
    # attributes permite generar SQL en pruebas con una URL ficticia, sin secretos.
    value = config.attributes.get("database_url") or os.getenv("DATABASE_URL")
    if not value:
        project_root = Path(__file__).resolve().parents[1]
        value = dotenv_values(project_root / ".env").get("DATABASE_URL")
    if not value:
        raise CommandError("Configura DATABASE_URL antes de utilizar Alembic.")
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise CommandError("Las migraciones de MITBOL requieren PostgreSQL.")
    return url


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


def run_migrations_online() -> None:
    engine = create_engine(get_database_url(), poolclass=pool.NullPool)
    try:
        with engine.begin() as connection:
            validate_initial_database(connection)
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

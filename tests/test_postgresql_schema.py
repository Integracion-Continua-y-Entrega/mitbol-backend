"""Verificación optativa del esquema instalado; todas las consultas son de lectura.

Activar con MITBOL_POSTGRES_TESTS=1. No crea tablas, inserta filas ni borra datos.
"""

import os

import pytest
from alembic.runtime.migration import MigrationContext
from sqlalchemy import UniqueConstraint, create_engine, inspect
from sqlalchemy.engine import make_url

from app.models import Base


@pytest.fixture(scope="module")
def postgres_connection():
    if os.getenv("MITBOL_POSTGRES_TESTS") != "1":
        pytest.skip("Requiere MITBOL_POSTGRES_TESTS=1 y mitbol_dev migrada")

    # Comprueba la misma configuración efectiva que utiliza la aplicación.
    from app.core.config import Settings

    url = make_url(Settings().database_url)
    assert url.get_backend_name() == "postgresql"
    assert url.database == "mitbol_dev", "Las verificaciones sólo admiten mitbol_dev"
    engine = create_engine(url, connect_args={
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
    })
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            assert connection.exec_driver_sql("SELECT current_database()").scalar_one() == "mitbol_dev"
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
            try:
                yield connection
            finally:
                connection.rollback()
    finally:
        engine.dispose()


def test_postgresql_database_tables_and_revision(postgres_connection):
    inspector = inspect(postgres_connection)
    assert set(inspector.get_table_names(schema="public")) == {
        *Base.metadata.tables, "alembic_version",
    }
    assert MigrationContext.configure(postgres_connection).get_current_heads() == (
        "20261029_0001",
    )


@pytest.mark.parametrize("table_name", sorted(Base.metadata.tables))
def test_postgresql_table_matches_orm(postgres_connection, table_name):
    """Compara las columnas y restricciones reales con el modelo SQLAlchemy."""
    inspector = inspect(postgres_connection)
    expected = Base.metadata.tables[table_name]
    actual = {
        column["name"]: column
        for column in inspector.get_columns(table_name, schema="public")
    }
    assert set(actual) == set(expected.columns.keys())

    for column in expected.columns:
        reflected = actual[column.name]
        assert reflected["type"].compile(dialect=postgres_connection.dialect) == (
            column.type.compile(dialect=postgres_connection.dialect)
        ), column.name
        assert reflected["nullable"] == column.nullable, column.name
        if column.server_default is not None:
            assert reflected["default"] == str(column.server_default.arg), column.name
        elif column.primary_key and column.autoincrement is not False and len(expected.primary_key.columns) == 1:
            assert reflected["autoincrement"] is True, column.name
            assert reflected["default"].startswith("nextval("), column.name
        else:
            assert reflected["default"] is None, column.name

    primary_key = inspector.get_pk_constraint(table_name, schema="public")
    assert primary_key["constrained_columns"] == list(expected.primary_key.columns.keys())
    assert primary_key["name"] == expected.primary_key.name

    unique_constraints = inspector.get_unique_constraints(table_name, schema="public")
    assert {
        (constraint["name"], tuple(constraint["column_names"]))
        for constraint in unique_constraints
    } == {
        (constraint.name, tuple(constraint.columns.keys()))
        for constraint in expected.constraints if isinstance(constraint, UniqueConstraint)
    }

    foreign_keys = inspector.get_foreign_keys(table_name, schema="public")
    assert {
        (constraint["name"], tuple(constraint["constrained_columns"]),
         constraint["referred_table"], tuple(constraint["referred_columns"]),
         constraint["options"].get("ondelete"))
        for constraint in foreign_keys
    } == {
        (constraint.name, tuple(constraint.column_keys),
         constraint.referred_table.name,
         tuple(element.column.name for element in constraint.elements), constraint.ondelete)
        for constraint in expected.foreign_key_constraints
    }

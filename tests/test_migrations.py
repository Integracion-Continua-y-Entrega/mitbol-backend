"""SQL PostgreSQL y aislamiento de Alembic, sin conexiones a la base real."""

import importlib
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Column, Integer, MetaData, String, Table, UniqueConstraint
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine

from app.models import Base
from app.models.legacy_base import LegacyBase
from migrations.validation import validate_initial_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_TABLES = {
    "Products", "ProductVariants", "ProductModifiers",
    "ProductComplements", "ProductModifierAssignments",
}
ENRICHMENT_TABLES = {"ProductImages", "Tags", "ProductTags"}
INITIAL_REVISION = "20261029_0001"
ENRICHMENT_REVISION = "20261102_0002"
AVAILABILITY_REVISION = "20261102_0003"


def alembic_config(output=None):
    config = Config(str(PROJECT_ROOT / "alembic.ini"), output_buffer=output)
    # No existe este servidor: la generación offline no debe intentar conectarse.
    config.attributes["database_url"] = "postgresql+psycopg2://offline.invalid/mitbol_dev"
    return config


def test_only_approved_models_are_in_current_metadata():
    # Registrar el prototipo explícitamente evita depender del orden de pytest.
    importlib.import_module("app.models.item")
    importlib.import_module("app.models.category")
    assert set(Base.metadata.tables) == CATALOG_TABLES | ENRICHMENT_TABLES
    assert {"items", "categories"}.isdisjoint(Base.metadata.tables)
    assert {"items", "categories"} == set(LegacyBase.metadata.tables)


def table_signature(table):
    dialect = postgresql.dialect()
    return {
        "columns": {
            column.name: (
                column.type.compile(dialect=dialect), column.nullable,
                str(column.server_default.arg) if column.server_default else None,
            ) for column in table.columns
        },
        "pk": tuple(column.name for column in table.primary_key.columns),
        "unique": {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
        },
        "fk": {
            (foreign_key.parent.name, foreign_key.target_fullname, foreign_key.ondelete)
            for foreign_key in table.foreign_keys
        },
        "constraint_names": {constraint.name for constraint in table.constraints},
    }


@pytest.mark.parametrize("revision_id,expected_tables", [
    (INITIAL_REVISION, CATALOG_TABLES),
    (ENRICHMENT_REVISION, ENRICHMENT_TABLES),
])
def test_migration_matches_models(monkeypatch, revision_id, expected_tables):
    """Compara tipos, nulabilidad, defaults y claves de la revisión y el ORM."""
    revision = ScriptDirectory.from_config(alembic_config()).get_revision(revision_id)
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": StringIO()},
    )
    operations = Operations(context)
    created_tables = {}

    def record_table(*args, **kwargs):
        table = operations.create_table(*args, **kwargs)
        created_tables[table.name] = table
        return table

    monkeypatch.setattr(revision.module, "op", SimpleNamespace(create_table=record_table))
    revision.module.upgrade()
    assert set(created_tables) == expected_tables
    for name in expected_tables:
        expected = table_signature(Base.metadata.tables[name])
        if name == "ProductVariants":
            expected["columns"].pop("is_commercially_available")
        assert table_signature(created_tables[name]) == expected, name


def test_upgrade_offline_creates_no_legacy_tables_and_never_connects(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("La generación offline intentó conectarse a una base de datos")

    monkeypatch.setattr(Engine, "connect", unexpected_connection)
    output = StringIO()
    command.upgrade(alembic_config(output), "head", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 9  # Ocho de negocio y alembic_version.
    for name in CATALOG_TABLES | ENRICHMENT_TABLES:
        assert f'CREATE TABLE "{name}"' in sql
    assert "CREATE TABLE alembic_version" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "NUMERIC NOT NULL" in sql
    assert "DROP TABLE" not in sql
    assert "categories" not in sql.lower()
    assert "CREATE DATABASE" not in sql
    assert "20261029_0001" in sql
    assert ENRICHMENT_REVISION in sql


def test_hu007_extends_hu005_without_changing_its_ancestry():
    scripts = ScriptDirectory.from_config(alembic_config())
    assert scripts.get_heads() == [AVAILABILITY_REVISION]
    assert scripts.get_revision(AVAILABILITY_REVISION).down_revision == ENRICHMENT_REVISION
    assert scripts.get_revision(ENRICHMENT_REVISION).down_revision == INITIAL_REVISION


def test_hu005_incremental_sql_creates_only_three_tables_without_connecting(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("El SQL de revisión intentó abrir una conexión")

    monkeypatch.setattr(Engine, "connect", unexpected_connection)
    output = StringIO()
    command.upgrade(alembic_config(output), f"{INITIAL_REVISION}:{ENRICHMENT_REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 3
    for name in ENRICHMENT_TABLES:
        assert f'CREATE TABLE "{name}"' in sql
    for name in CATALOG_TABLES:
        assert f'CREATE TABLE "{name}"' not in sql
        assert f'ALTER TABLE "{name}"' not in sql
    assert "product_image_id SERIAL NOT NULL" in sql
    assert "tag_id SERIAL NOT NULL" in sql
    assert sql.count("DEFAULT now() NOT NULL") == 2
    assert 'PRIMARY KEY (product_variant_id, tag_id)' in sql
    assert 'UNIQUE (name)' in sql
    assert sql.count("FOREIGN KEY") == 3
    assert "ON DELETE" not in sql  # NO ACTION implícito, como HU-008.
    assert "ON UPDATE" not in sql
    for operation in ("ALTER TABLE", "DROP ", "TRUNCATE ", "INSERT INTO", "DELETE FROM"):
        assert operation not in sql
    assert sql.count("UPDATE alembic_version") == 1
    review_sql = (PROJECT_ROOT / "docs/database/hu005_pending.sql").read_text(encoding="utf-8")
    normalize_sql = lambda value: "\n".join(line.rstrip() for line in value.splitlines()).strip()
    assert normalize_sql(review_sql[review_sql.index("BEGIN;"):]) == normalize_sql(sql)


def test_hu005_downgrade_sql_removes_only_enrichment_tables():
    output = StringIO()
    command.downgrade(
        alembic_config(output), f"{ENRICHMENT_REVISION}:{INITIAL_REVISION}", sql=True,
    )
    sql = output.getvalue()
    assert sql.count("DROP TABLE") == 3
    assert sql.index('DROP TABLE "ProductTags"') < sql.index('DROP TABLE "Tags"')
    assert 'DROP TABLE "ProductImages"' in sql
    for name in CATALOG_TABLES:
        assert f'DROP TABLE "{name}"' not in sql
    assert "CASCADE" not in sql


def test_downgrade_offline_removes_children_before_parents():
    output = StringIO()
    command.downgrade(alembic_config(output), "20261029_0001:base", sql=True)
    sql = output.getvalue()
    assert sql.index('DROP TABLE "ProductModifierAssignments"') < sql.index('DROP TABLE "ProductVariants"')
    assert sql.index('DROP TABLE "ProductComplements"') < sql.index('DROP TABLE "ProductModifiers"')
    assert sql.index('DROP TABLE "ProductVariants"') < sql.index('DROP TABLE "Products"')
    assert "categories" not in sql.lower()
    assert "CASCADE" not in sql


def test_initial_validation_accepts_empty_database_without_creating_tables(empty_postgresql_connection):
    validate_initial_database(empty_postgresql_connection)
    assert inspect(empty_postgresql_connection).get_table_names() == []


def test_initial_validation_rejects_prototype_without_modifying_it(empty_postgresql_connection):
    metadata = MetaData()
    Table("items", metadata, Column("id", Integer, primary_key=True))
    Table("categories", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(empty_postgresql_connection)
    with pytest.raises(CommandError, match="base nueva y vacía"):
        validate_initial_database(empty_postgresql_connection)
    assert set(inspect(empty_postgresql_connection).get_table_names()) == {"items", "categories"}


def test_initial_validation_allows_existing_alembic_revision(empty_postgresql_connection):
    metadata = MetaData()
    version = Table("alembic_version", metadata, Column("version_num", String(32), primary_key=True))
    Table("Products", metadata, Column("product_id", Integer, primary_key=True))
    metadata.create_all(empty_postgresql_connection)
    empty_postgresql_connection.execute(version.insert().values(version_num="20261029_0001"))
    validate_initial_database(empty_postgresql_connection)


def test_supplied_connection_must_also_be_postgresql():
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.connect() as connection:
            config = alembic_config()
            config.attributes["connection"] = connection
            with pytest.raises(CommandError, match="requieren PostgreSQL"):
                command.current(config)
            assert inspect(connection).get_table_names() == []
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_fastapi_import_does_not_create_tables_or_connect(monkeypatch):
    def unexpected_database_access(*args, **kwargs):
        pytest.fail("Importar FastAPI intentó crear tablas o abrir una conexión")

    monkeypatch.setattr(MetaData, "create_all", unexpected_database_access)
    monkeypatch.setattr(Engine, "connect", unexpected_database_access)
    main = importlib.reload(importlib.import_module("app.main"))
    from app.db.database import get_db

    def isolated_menu_db():
        # El menú ahora consulta datos; esta prueba de importación no abre conexiones.
        yield SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: []))

    main.app.dependency_overrides[get_db] = isolated_menu_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            assert (await client.get("/")).status_code == 200
            menu = await client.get("/api/menu")
            assert menu.status_code == 200
            assert menu.json() == {"currency": "MXN", "products": []}
            assert (await client.post("/api/v1/sync/loyverse")).status_code == 404
    finally:
        main.app.dependency_overrides.pop(get_db, None)

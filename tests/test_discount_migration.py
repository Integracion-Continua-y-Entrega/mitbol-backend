"""HU-009: cadena Alembic copiada y PostgreSQL temporal; sin activar head real."""

import importlib.util
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from decimal import Decimal
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine


ROOT = Path(__file__).resolve().parents[1]
PENDING = ROOT / "migrations/pending/20261111_0006_hu009_discounts.py"
PREVIOUS = "20261106_0005"
REVISION = "20261111_0006"


def test_upsert_preserves_identity_scope_and_handles_variable_and_deleted(
    empty_postgresql_connection, staged_config,
):
    from app.services.discount_statements import build_discount_upsert

    connection = empty_postgresql_connection
    staged_config.attributes["connection"] = connection
    command.upgrade(staged_config, REVISION)
    table = Table("Discounts", MetaData(), autoload_with=connection)
    values = dict(
        loyverse_id="497f6eca-6276-4993-bfeb-53cbbbba6f08",
        name="Inicial", discount_type="FIXED_AMOUNT", value=Decimal("35.25"),
        scope="sale", is_active=True,
        synced_at=datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
    )

    def write():
        connection.execute(build_discount_upsert(table, values))
        return connection.execute(select(table)).mappings().one()

    original = write()
    assert original["value"] == Decimal("35.25")
    assert write() == original  # Repetir no duplica ni cambia la identidad local.
    values.update(name="Variable", discount_type="VARIABLE_AMOUNT", value=None,
                  scope="unknown", is_active=False,
                  synced_at=datetime(2026, 11, 11, 13, tzinfo=timezone.utc))
    updated = write()
    assert updated["discount_id"] == original["discount_id"]
    assert updated["created_at"] == original["created_at"]
    assert updated["loyverse_id"] == original["loyverse_id"]
    assert updated["scope"] == "sale"
    assert updated["value"] is None
    assert updated["is_active"] is False
    assert updated["name"] == "Variable"
    assert updated["discount_type"] == "VARIABLE_AMOUNT"
    assert updated["synced_at"] == values["synced_at"]
    values.update(scope="receipt", is_active=True)
    assert write()["scope"] == "receipt"
    assert write()["is_active"] is True


@pytest.fixture
def staged_config(tmp_path):
    directory = tmp_path / "migrations"
    shutil.copytree(ROOT / "migrations", directory, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(PENDING, directory / "versions" / PENDING.name)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(directory))
    config.attributes["database_url"] = "postgresql+psycopg2://offline.invalid/mitbol_unused"
    return config


def signature(table):
    dialect = postgresql.dialect()
    return dict(
        columns={c.name: [c.type.compile(dialect=dialect), c.nullable,
                         str(c.server_default.arg) if c.server_default else None] for c in table.columns},
        pk=[c.name for c in table.primary_key.columns],
        constraints=sorted(c.name for c in table.constraints),
    )


def test_pending_revision_and_model_match_without_registering_global_metadata(monkeypatch):
    spec = importlib.util.spec_from_file_location("pending_discount_migration", PENDING)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    tables = {}

    def record(name, *columns, **kwargs):
        tables[name] = Table(name, MetaData(), *columns, **kwargs)

    monkeypatch.setattr(revision, "op", SimpleNamespace(create_table=record))
    revision.upgrade()
    # Importar el modelo en proceso separado evita contaminar los esquemas históricos.
    program = """
import json
from sqlalchemy.dialects import postgresql
from app.models.discount import Discount
t=Discount.__table__
print(json.dumps(dict(columns={c.name:[c.type.compile(dialect=postgresql.dialect()),c.nullable,
str(c.server_default.arg) if c.server_default else None] for c in t.columns},
pk=[c.name for c in t.primary_key.columns],constraints=sorted(c.name for c in t.constraints))))
"""
    result = subprocess.run([sys.executable, "-B", "-c", program], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    assert signature(tables["Discounts"]) == json.loads(result.stdout)
    assert revision.down_revision == PREVIOUS


def test_staged_upgrade_sql_only_creates_discounts_and_never_connects(staged_config, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La generación offline intentó abrir una conexión")
    monkeypatch.setattr(Engine, "connect", forbidden)
    output = StringIO()
    staged_config.output_buffer = output
    command.upgrade(staged_config, f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1
    assert 'CREATE TABLE "Discounts"' in sql
    assert 'scope VARCHAR(20) NOT NULL' in sql
    assert 'value NUMERIC,' in sql
    assert all(operation not in sql for operation in ["DROP TABLE", "ALTER TABLE", "CREATE ROLE", "GRANT ", "TRUNCATE"])
    assert ScriptDirectory.from_config(staged_config).get_heads() == [REVISION]
    assert ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_heads() == [PREVIOUS]


def test_staged_migration_preserves_catalog_and_roundtrips_schema(empty_postgresql_connection, staged_config):
    connection = empty_postgresql_connection
    staged_config.attributes["connection"] = connection
    command.upgrade(staged_config, PREVIOUS)
    before_tables = set(inspect(connection).get_table_names())
    metadata = MetaData()
    metadata.reflect(connection)
    products = metadata.tables["Products"]
    marker = connection.scalar(products.insert().values(
        loyverse_id="hu009-catalog-marker", name="Conservar producto", is_featured=True,
        is_new=False, synced_at="2026-11-11T12:00:00+00:00",
    ).returning(products.c.product_id))
    before = connection.execute(select(products)).mappings().all()

    command.upgrade(staged_config, REVISION)
    assert set(inspect(connection).get_table_names()) == before_tables | {"Discounts"}
    assert MigrationContext.configure(connection).get_current_heads() == (REVISION,)
    discounts = Table("Discounts", MetaData(), autoload_with=connection)
    identifier = connection.scalar(discounts.insert().values(
        loyverse_id="497f6eca-6276-4993-bfeb-53cbbbba6f08", name="Variable",
        discount_type="VARIABLE_AMOUNT", value=None, scope="unknown",
    ).returning(discounts.c.discount_id))
    row = connection.execute(select(discounts).where(discounts.c.discount_id == identifier)).mappings().one()
    assert row["value"] is None and row["is_active"] is True
    assert row["created_at"] is not None and row["synced_at"] is None
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(discounts.insert().values(
            loyverse_id=row["loyverse_id"], name="Duplicado", discount_type="VARIABLE_AMOUNT", scope="unknown",
        ))
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(discounts.insert().values(loyverse_id="missing-scope", name="Sin alcance", discount_type="VARIABLE_AMOUNT"))
    exact = Decimal("35.1234567890123456789")
    fixed = connection.scalar(discounts.insert().values(
        loyverse_id="hu009-exact", name="Exacto", discount_type="FIXED_AMOUNT", value=exact, scope="unknown",
    ).returning(discounts.c.discount_id))
    assert connection.scalar(select(discounts.c.value).where(discounts.c.discount_id == fixed)) == exact
    assert connection.execute(select(products)).mappings().all() == before

    command.downgrade(staged_config, PREVIOUS)
    assert set(inspect(connection).get_table_names()) == before_tables
    assert connection.execute(select(products)).mappings().all() == before
    assert connection.scalar(select(products.c.product_id).where(products.c.product_id == marker)) == marker

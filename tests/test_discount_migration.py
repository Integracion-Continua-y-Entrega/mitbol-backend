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
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
PENDING = ROOT / "migrations/pending/20261111_0006_hu009_discounts.py"
PREVIOUS = "20261106_0005"
REVISION = "20261111_0006"


@pytest.fixture
def discount_service(empty_postgresql_connection, staged_config):
    from app.services.discount_sync_service import DiscountSyncService

    connection = empty_postgresql_connection
    staged_config.attributes["connection"] = connection
    command.upgrade(staged_config, REVISION)
    sessions = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    service = DiscountSyncService(
        sessions, expected_database=connection.engine.url.database,
        store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12",
    )
    table = Table("Discounts", MetaData(), schema="public", autoload_with=connection)
    return SimpleNamespace(service=service, table=table, connection=connection)


def discount_snapshot(*, name="Puntos", identifier="497f6eca-6276-4993-bfeb-53cbbbba6f08"):
    from app.services.discount_probe import DiscountSnapshot
    from app.services.discount_rules import DiscountType
    from app.services.discount_transform import DiscountRecord

    stamp = datetime(2026, 11, 12, tzinfo=timezone.utc)
    return DiscountSnapshot((DiscountRecord(
        loyverse_id=identifier, name=name, discount_type=DiscountType.DISCOUNT_BY_POINTS,
        value=None, stores=(), restricted_access=False,
        created_at=stamp, updated_at=stamp, deleted_at=None,
    ),), True, stamp)


def test_coordinator_persists_points_and_empty_does_not_delete(discount_service):
    from app.services.discount_probe import DiscountSnapshot

    b = discount_service
    snapshot = discount_snapshot()
    assert b.service.persist_snapshot(snapshot) == 1
    before = b.connection.execute(select(b.table)).mappings().all()
    assert before[0]["discount_type"] == "DISCOUNT_BY_POINTS"
    assert before[0]["value"] is None
    assert b.service.persist_snapshot(snapshot) == 1
    assert b.service.persist_snapshot(DiscountSnapshot((), True, snapshot.fetched_at)) == 0
    assert b.connection.execute(select(b.table)).mappings().all() == before


def test_coordinator_sql_failure_rolls_back_and_sanitizes(discount_service):
    from app.services.discount_probe import DiscountSnapshot
    from app.services.discount_sync_service import DiscountSyncError

    b = discount_service
    original = discount_snapshot()
    b.service.persist_snapshot(original)
    before = b.connection.execute(select(b.table)).mappings().all()
    changed = discount_snapshot(name="Cambio que se revierte")
    invalid = discount_snapshot(name=None, identifier="synthetic-error")
    batch = DiscountSnapshot(changed.records + invalid.records, True, original.fetched_at)
    with pytest.raises(DiscountSyncError, match="Falló la persistencia") as error:
        b.service.persist_snapshot(batch)
    assert "INSERT" not in str(error.value)
    assert b.connection.execute(select(b.table)).mappings().all() == before


def test_coordinator_rejects_real_database_mismatch(discount_service):
    from app.services.discount_sync_service import DiscountSyncError

    b = discount_service
    b.service.expected_database = "mitbol_test_hu008_000000000000"
    with pytest.raises(DiscountSyncError, match="conexión real"):
        b.service.persist_snapshot(discount_snapshot())
    assert b.connection.execute(select(b.table)).all() == []


def test_coordinator_rejects_busy_commercial_lock(discount_service):
    from app.services.discount_sync_service import DiscountSyncError
    from app.services.sync_service import CATALOG_SYNC_LOCK_KEY
    from sqlalchemy import func

    b = discount_service
    with b.connection.engine.connect() as other:
        other.scalar(select(func.pg_advisory_lock(CATALOG_SYNC_LOCK_KEY)))
        try:
            with pytest.raises(DiscountSyncError, match="en curso"):
                b.service.persist_snapshot(discount_snapshot())
        finally:
            other.scalar(select(func.pg_advisory_unlock(CATALOG_SYNC_LOCK_KEY)))
    assert b.connection.execute(select(b.table)).all() == []


def test_coordinator_requires_discount_revision_before_provider(empty_postgresql_connection, staged_config):
    import asyncio
    from unittest.mock import AsyncMock
    from app.services.discount_sync_service import DiscountSyncError, DiscountSyncService

    connection = empty_postgresql_connection
    staged_config.attributes["connection"] = connection
    command.upgrade(staged_config, PREVIOUS)
    service = DiscountSyncService(
        sessionmaker(bind=connection, join_transaction_mode="create_savepoint"),
        expected_database=connection.engine.url.database,
        store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12",
    )
    client = SimpleNamespace(get_discounts=AsyncMock())
    with pytest.raises(DiscountSyncError, match="20261111_0006"):
        asyncio.run(service.synchronize(client))
    client.get_discounts.assert_not_awaited()


def test_coordinator_invalid_provider_batch_preserves_data(discount_service):
    import asyncio
    from unittest.mock import AsyncMock
    from app.services.discount_transform import DiscountValidationError

    b = discount_service
    b.service.persist_snapshot(discount_snapshot())
    before = b.connection.execute(select(b.table)).mappings().all()
    client = SimpleNamespace(get_discounts=AsyncMock(return_value=[{"id": "invalid"}]))
    with pytest.raises(DiscountValidationError):
        asyncio.run(b.service.synchronize(client))
    client.get_discounts.assert_awaited_once_with(include_deleted=True)
    assert b.connection.execute(select(b.table)).mappings().all() == before


def test_read_service_returns_schemas_and_preserves_transaction(empty_postgresql_connection, staged_config):
    from app.schemas.discounts import DiscountRead
    from app.services.discount_read_service import list_discounts

    connection = empty_postgresql_connection
    staged_config.attributes["connection"] = connection
    command.upgrade(staged_config, REVISION)
    table = Table("Discounts", MetaData(), autoload_with=connection)
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        with session.begin():
            assert list_discounts(session, table) == []
            session.execute(table.insert().values(
                loyverse_id="read-service-fixed", name="Inactivo",
                discount_type="FIXED_AMOUNT", value=Decimal("35.1234567890123456789"),
                scope="unknown", is_active=False,
            ))
            session.execute(table.insert().values(
                loyverse_id="read-service-variable", name="Variable",
                discount_type="VARIABLE_AMOUNT", value=None, scope="unknown",
            ))
            transaction = session.get_transaction()
            result = list_discounts(session, table, limit=1)
            assert len(result) == 1 and isinstance(result[0], DiscountRead)
            assert result[0].is_active is False
            assert result[0].model_dump(mode="json")["value"] == "35.1234567890123456789"
            assert list_discounts(session, table, limit=1, offset=1)[0].value is None
            assert list_discounts(session, table, offset=2) == []
            assert session.get_transaction() is transaction and transaction.is_active
        assert len(connection.execute(select(table)).all()) == 2


def test_admin_query_orders_pages_and_includes_inactive(empty_postgresql_connection, staged_config):
    from app.schemas.discounts import DiscountRead
    from app.services.discount_queries import build_discount_list

    connection = empty_postgresql_connection
    staged_config.attributes["connection"] = connection
    command.upgrade(staged_config, REVISION)
    table = Table("Discounts", MetaData(), autoload_with=connection)
    assert connection.execute(build_discount_list(table)).mappings().all() == []
    for identifier, active in [(30, True), (10, False), (20, True)]:
        connection.execute(table.insert().values(
            discount_id=identifier, loyverse_id=f"synthetic-{identifier}",
            name="Consulta", discount_type="VARIABLE_AMOUNT", value=None,
            scope="unknown", is_active=active,
        ))
    first = connection.execute(build_discount_list(table, limit=2)).mappings().all()
    second = connection.execute(build_discount_list(table, limit=2, offset=2)).mappings().all()
    assert [row["discount_id"] for row in first] == [10, 20]
    assert [row["discount_id"] for row in second] == [30]
    assert first[0]["is_active"] is False
    assert DiscountRead.model_validate(first[0]).value is None
    assert connection.execute(build_discount_list(table, offset=3)).all() == []


@pytest.mark.parametrize("options", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"limit": "10"},
    {"offset": -1}, {"offset": False}, {"offset": 1.5},
])
def test_admin_query_rejects_invalid_pagination(options):
    from app.services.discount_queries import build_discount_list

    with pytest.raises(ValueError):
        build_discount_list(Table("Discounts", MetaData()), **options)


def test_writer_requires_explicit_transaction():
    from app.services.discount_writer import write_discount_batch

    with Session() as session:
        with pytest.raises(ValueError, match="transacción activa"):
            write_discount_batch(session, Table("Discounts", MetaData()), ())


def test_writer_batch_success_repeat_empty_and_rollback(empty_postgresql_connection, staged_config):
    from app.services.discount_writer import write_discount_batch

    connection = empty_postgresql_connection
    staged_config.attributes["connection"] = connection
    command.upgrade(staged_config, REVISION)
    table = Table("Discounts", MetaData(), autoload_with=connection)
    row = dict(
        loyverse_id="497f6eca-6276-4993-bfeb-53cbbbba6f08", name="Conservar",
        discount_type="FIXED_AMOUNT", value=Decimal("35.25"), scope="unknown",
        is_active=True, synced_at=datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
    )
    # SAVEPOINT permite probar la transacción del servicio sin confirmar el
    # esquema temporal que la fixture revierte al terminar cada prueba.
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        with session.begin():
            assert write_discount_batch(session, table, (row,)) == 1
        before = connection.execute(select(table)).mappings().all()
        with session.begin():
            assert write_discount_batch(session, table, (row,)) == 1
            assert write_discount_batch(session, table, ()) == 0
        assert connection.execute(select(table)).mappings().all() == before

        # La primera fila cambia datos y la segunda viola NOT NULL. El error
        # debe escapar de la función para que session.begin() revierta ambas.
        with pytest.raises(IntegrityError):
            with session.begin():
                write_discount_batch(session, table, (
                    dict(row, name="No conservar este cambio", is_active=False),
                    dict(row, loyverse_id="other-discount", name=None),
                ))
        assert connection.execute(select(table)).mappings().all() == before

        # Un error al construir la última sentencia tampoco debe ejecutar la primera.
        with pytest.raises(ValueError):
            with session.begin():
                write_discount_batch(session, table, (
                    dict(row, name="Tampoco cambiar"), {"loyverse_id": "incompleto"},
                ))
        assert connection.execute(select(table)).mappings().all() == before


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

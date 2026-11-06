"""PostgreSQL aislado; en GitHub Actions nunca se consulta mitbol_dev ni .env."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select, func
from sqlalchemy.engine import make_url, URL
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.services.catalog_probe import CatalogSnapshot
from app.services.sync_service import SyncService


def _ci_database_url():
    """En GitHub sólo admite el servicio temporal definido por el workflow."""
    if os.getenv("GITHUB_ACTIONS") != "true":
        return None
    try:
        url = make_url(os.environ["DATABASE_URL"])
        valid = (
            url.drivername == "postgresql+psycopg2"
            and url.host == "127.0.0.1"
            and url.database == "mitbol_test_ci"
            and url.username == "mitbol_ci"
            and not url.query
        )
    except (KeyError, ArgumentError, ValueError):
        valid = False
    if not valid:
        raise pytest.UsageError(
            "CI requiere DATABASE_URL del servicio PostgreSQL temporal: "
            "usuario mitbol_ci, host 127.0.0.1 y base mitbol_test_ci, sin parámetros."
        )
    return url


def pytest_configure(config):
    if _ci_database_url() is not None and any(
        os.getenv(flag) != "1"
        for flag in ("MITBOL_SYNC_POSTGRES_TESTS", "MITBOL_POSTGRES_TESTS")
    ):
        raise pytest.UsageError("CI requiere ambas suites PostgreSQL activadas con valor 1.")


@pytest.fixture
def commercial_snapshot():
    def variant(identifier, parent, name, price):
        return {
            "variant_id": identifier, "item_id": parent, "option1_value": name,
            "default_pricing_type": "FIXED", "default_price": price,
            "stores": [{"store_id": "test-store", "pricing_type": "FIXED",
                        "price": price, "available_for_sale": True}],
        }

    items = (
        {"id": "burger", "item_name": "Hamburguesa", "modifier_ids": ["extras"],
         "variants": [variant("single", "burger", "Sencilla", 89), variant("double", "burger", "Doble", 119)]},
        {"id": "drink", "item_name": "Limonada", "modifier_ids": ["herbs"],
         "variants": [variant("small", "drink", "355 ml", 35), variant("large", "drink", "500 ml", 49)]},
        {"id": "fries", "item_name": "Papas", "modifier_ids": [],
         "variants": [variant("regular", "fries", None, 45)]},
    )
    modifiers = (
        {"id": "extras", "name": "Extras", "position": 1, "stores": ["test-store"],
         "modifier_options": [
             {"id": "cheese", "name": "Queso", "price": 15, "position": 1},
             {"id": "bacon", "name": "Tocino", "price": 20, "position": 2},
             {"id": "no-onion", "name": "Sin cebolla", "price": 0, "position": 3},
         ]},
        {"id": "herbs", "name": "Hierbas", "position": 2, "stores": ["test-store"],
         "modifier_options": [
             {"id": "chia", "name": "Chía", "price": 5, "position": 1},
             {"id": "mint", "name": "Menta", "price": 5, "position": 2},
         ]},
    )
    return CatalogSnapshot(
        {"id": "test-store", "name": "Tienda simulada"}, items, modifiers, True,
        datetime(2026, 11, 1, tzinfo=timezone.utc),
    )


def _development_state(url):
    """Captura esquema, filas y secuencias sin asumir que HU-005 esté aplicada."""
    engine = create_engine(url, connect_args={
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
    })
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            assert connection.scalar(select(func.current_database())) == "mitbol_dev"
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
            inspector = inspect(connection)
            tables = {}
            for name in sorted(inspector.get_table_names(schema="public")):
                table = Table(name, MetaData(), schema="public", autoload_with=connection)
                rows = [dict(row) for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns)
                ).mappings()]
                tables[name] = {
                    "columns": [(column.name, column.type.compile(dialect=connection.dialect),
                                 column.nullable, str(column.server_default.arg)
                                 if column.server_default is not None else None)
                                for column in table.columns],
                    "pk": inspector.get_pk_constraint(name, schema="public"),
                    "fk": inspector.get_foreign_keys(name, schema="public"),
                    "unique": inspector.get_unique_constraints(name, schema="public"),
                    "count": len(rows),
                    "rows_sha256": sha256(json.dumps(
                        rows, sort_keys=True, default=str, ensure_ascii=False,
                    ).encode("utf-8")).hexdigest(),
                }
            sequences = connection.exec_driver_sql(
                "SELECT sequencename, last_value FROM pg_sequences "
                "WHERE schemaname = 'public' ORDER BY sequencename"
            ).all()
            return {"tables": tables, "sequences": [tuple(row) for row in sequences]}
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def sync_test_database(record_testsuite_property):
    if os.getenv("MITBOL_SYNC_POSTGRES_TESTS") != "1":
        pytest.skip("Activar MITBOL_SYNC_POSTGRES_TESTS=1 para PostgreSQL aislado")
    if os.getenv("MITBOL_SYNC_TEST_CLUSTER"):
        # Etapa 3E: cluster desechable, sin .env, mitbol_dev ni Alembic.
        yield from _metadata_test_database(record_testsuite_property)
        return
    root = Path(__file__).resolve().parents[1]
    source_url = _ci_database_url()
    isolated_ci = source_url is not None
    if not isolated_ci:
        source_url = make_url(dotenv_values(root / ".env")["DATABASE_URL"])
        assert source_url.get_backend_name() == "postgresql"
        assert source_url.database == "mitbol_dev"
        before = _development_state(source_url)
    name = "mitbol_test_hu008_" + uuid4().hex[:12]
    maintenance = create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with maintenance.connect() as connection:
            assert connection.scalar(select(func.current_database())) == "postgres"
            quoted = connection.dialect.identifier_preparer.quote(name)
            connection.exec_driver_sql(f"CREATE DATABASE {quoted} TEMPLATE template0 ENCODING 'UTF8'")
    finally:
        maintenance.dispose()
    test_url = source_url.set(database=name)
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = test_url.render_as_string(hide_password=False)
    # Regresión del catálogo sobre la revisión autorizada que usa Render/Neon.
    # La autenticación prueba su head por separado en una base temporal propia.
    command.upgrade(config, "20261102_0003")
    engine = create_engine(test_url, connect_args={
        "connect_timeout": 5, "options": "-c statement_timeout=15000",
    })
    try:
        yield engine
    finally:
        with engine.connect() as connection:
            remaining = {table_name: connection.scalar(select(func.count()).select_from(table))
                         for table_name, table in Base.metadata.tables.items()}
        engine.dispose()
        # La evidencia puede incluirse en JUnit sin sobrescribir informes históricos.
        record_testsuite_property("isolated_postgresql_database", name)
        record_testsuite_property("remaining_test_rows", json.dumps(remaining))
        assert all(value == 0 for value in remaining.values())
        if not isolated_ci:
            after = _development_state(source_url)
            record_testsuite_property("development_unchanged", str(before == after))
            assert before == after, "El esquema, las filas o las secuencias de mitbol_dev cambiaron"


def _metadata_test_database(record_testsuite_property):
    directory = Path(os.environ["MITBOL_SYNC_TEST_CLUSTER"]).resolve()
    assert directory.name == "data"
    assert directory.parent.parent == Path(tempfile.gettempdir()).resolve()
    assert re.fullmatch(r"mitbol-sync-preflight-[a-f0-9]{32}", directory.parent.name)
    port = int(os.environ["MITBOL_SYNC_TEST_PORT"])
    assert 1024 <= port <= 65535
    url = URL.create("postgresql+psycopg2", username="mitbol_ci", host="127.0.0.1",
                     port=port, database="template1")
    control = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True,
                            connect_args={"connect_timeout": 5})
    name = "mitbol_test_hu008_" + uuid4().hex[:12]
    with control.connect() as connection:
        assert connection.exec_driver_sql("SELECT current_user").scalar_one() == "mitbol_ci"
        assert Path(connection.exec_driver_sql("SHOW data_directory").scalar_one()).resolve() == directory
        assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 18
        quoted = connection.dialect.identifier_preparer.quote(name)
        connection.exec_driver_sql(f"CREATE DATABASE {quoted} TEMPLATE template0 ENCODING 'UTF8'")
    engine = create_engine(url.set(database=name), hide_parameters=True)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            connection.exec_driver_sql("CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO public.alembic_version VALUES ('20261102_0003')")
        record_testsuite_property("isolated_postgresql_database", name)
        record_testsuite_property("schema_source", "synthetic metadata; no migrations")
        yield engine
    finally:
        engine.dispose()
        with control.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE {quoted} WITH (FORCE)")
        control.dispose()


@pytest.fixture
def sync_db(sync_test_database):
    engine = sync_test_database
    with engine.connect() as connection:
        transaction = connection.begin()
        sessions = sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False,
        )
        service = SyncService(sessions, expected_database=engine.url.database, store_id="test-store")
        try:
            yield SimpleNamespace(
                engine=engine, connection=connection, sessions=sessions, service=service,
            )
        finally:
            transaction.rollback()


@pytest.fixture(scope="session")
def empty_postgresql_database(sync_test_database, record_testsuite_property):
    """Base sin migraciones para validar el prototipo y el arranque de Alembic."""
    name = "mitbol_test_hu008_" + uuid4().hex[:12]
    url = sync_test_database.url.set(database=name)
    control_database = "template1" if os.getenv("MITBOL_SYNC_TEST_CLUSTER") else "postgres"
    maintenance = create_engine(url.set(database=control_database), isolation_level="AUTOCOMMIT")
    try:
        with maintenance.connect() as connection:
            assert connection.scalar(select(func.current_database())) == control_database
            quoted = connection.dialect.identifier_preparer.quote(name)
            connection.exec_driver_sql(f"CREATE DATABASE {quoted} TEMPLATE template0 ENCODING 'UTF8'")
    finally:
        maintenance.dispose()
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        yield engine
    finally:
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == []
        record_testsuite_property("empty_postgresql_database", name)
        engine.dispose()
        if os.getenv("MITBOL_SYNC_TEST_CLUSTER"):
            maintenance = create_engine(url.set(database=control_database), isolation_level="AUTOCOMMIT")
            try:
                with maintenance.connect() as connection:
                    connection.exec_driver_sql(f"DROP DATABASE {quoted} WITH (FORCE)")
            finally:
                maintenance.dispose()


@pytest.fixture
def empty_postgresql_connection(empty_postgresql_database):
    with empty_postgresql_database.connect() as connection, connection.begin():
        assert connection.scalar(select(func.current_database())).startswith("mitbol_test_hu008_")
        try:
            yield connection
        finally:
            connection.rollback()

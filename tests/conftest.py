"""Fixtures de HU-008: PostgreSQL aislado y rollback, sin escrituras en mitbol_dev."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.services.catalog_probe import CatalogSnapshot
from app.services.sync_service import SyncService


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


def _development_counts(url):
    engine = create_engine(url, connect_args={
        "connect_timeout": 5, "options": "-c default_transaction_read_only=on",
    })
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            assert connection.scalar(select(func.current_database())) == "mitbol_dev"
            return {name: connection.scalar(select(func.count()).select_from(table))
                    for name, table in Base.metadata.tables.items()}
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def sync_test_database():
    if os.getenv("MITBOL_SYNC_POSTGRES_TESTS") != "1":
        pytest.skip("Activar MITBOL_SYNC_POSTGRES_TESTS=1 para PostgreSQL aislado")
    root = Path(__file__).resolve().parents[1]
    source_url = make_url(dotenv_values(root / ".env")["DATABASE_URL"])
    assert source_url.get_backend_name() == "postgresql"
    assert source_url.database == "mitbol_dev"
    before = _development_counts(source_url)
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
    command.upgrade(config, "20261029_0001")
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
        after = _development_counts(source_url)
        report = {
            "test_database": name, "revision": "20261029_0001",
            "remaining_test_rows": remaining,
            "development_counts_before": before, "development_counts_after": after,
            "development_unchanged": before == after,
            "database_dropped": False,
        }
        output = root / "docs/loyverse/hu008_block2_test_database.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        assert all(value == 0 for value in remaining.values())
        assert before == after, "mitbol_dev cambió durante las pruebas"


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

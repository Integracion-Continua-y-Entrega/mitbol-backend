"""Migración incremental real con cinco variantes históricas en PostgreSQL aislado."""

from decimal import Decimal
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import MetaData, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, ProductVariant
from app.services.menu_service import get_menu
from app.services.sync_service import SyncService

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = "20261102_0002"
REVISION = "20261102_0003"


def config(output=None):
    value = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    value.attributes["database_url"] = "postgresql://offline.invalid/mitbol_dev"
    return value


def test_review_sql_adds_only_conservative_column_without_connecting(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("La generación SQL intentó conectarse")
    monkeypatch.setattr(Engine, "connect", forbidden)
    output = StringIO()
    command.upgrade(config(output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert 'ALTER TABLE "ProductVariants" ADD COLUMN is_commercially_available BOOLEAN DEFAULT false NOT NULL;' in sql
    assert sql.count("ALTER TABLE") == 1
    for operation in ("CREATE TABLE", "DROP ", "TRUNCATE ", "INSERT ", "DELETE ", 'UPDATE "ProductVariants"'):
        assert operation not in sql
    prepared = (ROOT / "docs/database/hu007_pending.sql").read_text(encoding="utf-8")
    normalize = lambda value: "\n".join(line.rstrip() for line in value.splitlines()).strip()
    assert normalize(prepared[prepared.index("BEGIN;"):]) == normalize(sql)


def test_historical_values_keys_sequences_and_enrichment_survive_upgrade(empty_postgresql_connection, commercial_snapshot):
    connection = empty_postgresql_connection
    migration_config = config()
    migration_config.attributes["connection"] = connection
    command.upgrade(migration_config, PREVIOUS)
    metadata = MetaData()
    metadata.reflect(connection)
    variants = metadata.tables["ProductVariants"]
    flags = [True, False, True, False, True]
    variant_ids = []
    for item in commercial_snapshot.items:
        products = metadata.tables["Products"]
        parent_id = connection.scalar(products.insert().values(
            loyverse_id=item["id"], name=item["item_name"], description="Local",
            synced_at=commercial_snapshot.fetched_at,
        ).returning(products.c.product_id))
        for variant in item["variants"]:
            index = len(variant_ids)
            variant_ids.append(connection.scalar(variants.insert().values(
                loyverse_id=variant["variant_id"], name=variant.get("option1_value") or item["item_name"],
                product_id=parent_id, price=Decimal(str(variant["stores"][0]["price"])),
                is_available=flags[index], synced_at=commercial_snapshot.fetched_at,
            ).returning(variants.c.product_variant_id)))
    images, tags, assignments = (metadata.tables[name] for name in ("ProductImages", "Tags", "ProductTags"))
    connection.execute(images.insert().values(product_variant_id=variant_ids[0], image_url="/media/keep.webp", display_order=0))
    tag_id = connection.scalar(tags.insert().values(name="picante").returning(tags.c.tag_id))
    connection.execute(assignments.insert().values(product_variant_id=variant_ids[0], tag_id=tag_id))
    def rows():
        # Proyecta exactamente las columnas históricas, incluso tras el upgrade.
        return {name: connection.execute(select(table).order_by(*table.primary_key.columns)).all()
                for name, table in metadata.tables.items() if name != "alembic_version"}
    before = rows()
    sequences_before = connection.exec_driver_sql("SELECT sequencename, last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").all()
    inspector = inspect(connection)
    keys_before = {name: (inspector.get_pk_constraint(name), inspector.get_foreign_keys(name), inspector.get_unique_constraints(name))
                   for name in metadata.tables}
    command.upgrade(migration_config, REVISION)
    assert rows() == before
    assert connection.exec_driver_sql("SELECT sequencename, last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").all() == sequences_before
    inspector = inspect(connection)
    assert {name: (inspector.get_pk_constraint(name), inspector.get_foreign_keys(name), inspector.get_unique_constraints(name))
            for name in metadata.tables} == keys_before
    assert connection.execute(select(ProductVariant.is_available).order_by(ProductVariant.product_variant_id)).scalars().all() == flags
    assert connection.execute(select(ProductVariant.is_commercially_available)).scalars().all() == [False] * 5
    context = MigrationContext.configure(connection, opts={"compare_type": True, "compare_server_default": True})
    assert context.get_current_heads() == (REVISION,)
    assert compare_metadata(context, Base.metadata) == []
    sessions = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    with sessions() as db:
        menu = get_menu(db)
        assert len([variant for item in menu.products for variant in item.variants]) == 5
        assert all(not item.can_order for item in menu.products)
        assert all(not variant.can_order for item in menu.products for variant in item.variants)
    # Simula la futura sincronización autorizada, exclusivamente en esta base aislada.
    sync = SyncService(sessions, expected_database=connection.engine.url.database, store_id="test-store")
    sync.persist_snapshot(commercial_snapshot)
    assert connection.execute(select(ProductVariant.is_available).order_by(ProductVariant.product_variant_id)).scalars().all() == flags
    assert connection.execute(select(ProductVariant.is_commercially_available)).scalars().all() == [True] * 5
    with sessions() as db:
        menu = get_menu(db)
        assert sum(variant.can_order for item in menu.products for variant in item.variants) == sum(flags)

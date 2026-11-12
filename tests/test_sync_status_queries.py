"""Agregaciones HU-010 sobre modelos reales en PostgreSQL temporal."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, event, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.models import Base, Discount, Product
from app.services.sync_status_queries import SYNC_STATUS_TABLES, build_sync_summary
from app.services.sync_status_service import get_sync_summaries
from app.schemas.sync_status import EntitySyncSummary, SyncEntity


@pytest.fixture
def connection(empty_postgresql_connection):
    # Solo la base temporal vacía: el esquema se revierte al terminar la prueba.
    Base.metadata.create_all(empty_postgresql_connection)
    return empty_postgresql_connection


@pytest.mark.parametrize("name", sorted(SYNC_STATUS_TABLES))
def test_empty_entity_returns_one_summary_with_zero_counts(connection, name):
    rows = connection.execute(build_sync_summary(Base.metadata.tables[name])).mappings().all()
    assert [dict(row) for row in rows] == [
        dict(total_count=0, synced_count=0, last_synced_at=None),
    ]


def test_counts_distinguish_null_dates_and_include_inactive_rows(connection):
    table = Discount.__table__
    latest = datetime(2026, 11, 12, 6, tzinfo=timezone(timedelta(hours=-6)))
    earlier = latest - timedelta(days=1)
    for index, stamp in enumerate([earlier, latest, None]):
        connection.execute(table.insert().values(
            loyverse_id=f"summary-{index}", name="Prueba", discount_type="VARIABLE_AMOUNT",
            scope="unknown", synced_at=stamp, is_active=index != 1,
        ))
    result = connection.execute(build_sync_summary(table)).mappings().one()
    assert dict(result) == dict(total_count=3, synced_count=2, last_synced_at=latest)
    assert result["last_synced_at"] == datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def test_existing_rows_without_any_sync_date_are_not_reported_as_empty(connection):
    connection.execute(Discount.__table__.insert().values(
        loyverse_id="without-date", name="Sin fecha", discount_type="VARIABLE_AMOUNT", scope="unknown",
    ))
    assert dict(connection.execute(build_sync_summary(Discount.__table__)).mappings().one()) == dict(
        total_count=1, synced_count=0, last_synced_at=None,
    )


def test_nonnullable_product_dates_are_all_counted(connection):
    stamp = datetime(2026, 11, 12, tzinfo=timezone.utc)
    connection.execute(Product.__table__.insert().values(
        loyverse_id="product-date", name="Producto", synced_at=stamp, is_active=False,
    ))
    assert dict(connection.execute(build_sync_summary(Product.__table__)).mappings().one()) == dict(
        total_count=1, synced_count=1, last_synced_at=stamp,
    )


@pytest.mark.parametrize("table", [
    Table("Persons", MetaData()),
    Table("Discounts", MetaData(), schema="unexpected"),
    Table("Discounts", MetaData(), Column("discount_id", Integer)),
])
def test_unsupported_table_is_rejected_without_execution(table):
    with pytest.raises(ValueError):
        build_sync_summary(table)


def test_service_returns_five_summaries_for_an_empty_database(connection):
    with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
        result = get_sync_summaries(db)
    assert len(result) == 5
    assert {row.entity for row in result} == set(SyncEntity)
    assert all(isinstance(row, EntitySyncSummary) for row in result)
    assert all(row.total_count == row.synced_count == 0 and row.last_synced_at is None for row in result)


def test_service_uses_one_query_and_preserves_caller_transaction(connection):
    stamp = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    connection.execute(Product.__table__.insert().values(
        loyverse_id="summary-product", name="Conservar", synced_at=stamp, is_active=False,
    ))
    connection.execute(Discount.__table__.insert().values(
        loyverse_id="summary-without-date", name="Sin fecha", discount_type="VARIABLE_AMOUNT", scope="unknown",
    ))
    before = connection.execute(select(Product.__table__)).mappings().all()
    observed = []

    def capture(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            observed.append(statement)

    with Session(bind=connection, join_transaction_mode="create_savepoint") as db, db.begin():
        transaction = db.get_transaction()
        event.listen(connection, "before_cursor_execute", capture)
        try:
            result = get_sync_summaries(db)
        finally:
            event.remove(connection, "before_cursor_execute", capture)
        assert db.get_transaction() is transaction and transaction.is_active
    assert len(observed) == 1 and "UNION ALL" in observed[0]
    rows = {row.entity: row for row in result}
    assert rows[SyncEntity.PRODUCTS].total_count == rows[SyncEntity.PRODUCTS].synced_count == 1
    assert rows[SyncEntity.PRODUCTS].last_synced_at == stamp
    assert rows[SyncEntity.DISCOUNTS].total_count == 1
    assert rows[SyncEntity.DISCOUNTS].synced_count == 0
    assert rows[SyncEntity.DISCOUNTS].last_synced_at is None
    assert connection.execute(select(Product.__table__)).mappings().all() == before


def test_missing_entity_fails_instead_of_reporting_partial_state(empty_postgresql_connection):
    connection = empty_postgresql_connection
    Base.metadata.create_all(connection, tables=[t for name, t in Base.metadata.tables.items() if name != "Discounts"])
    with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
        with pytest.raises(ProgrammingError), db.begin():
            get_sync_summaries(db)

"""Marca observable entre conexiones con transacciones reales, sin proveedor."""

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from test_discount_lifecycle import database
from test_discount_migration import staged_config
from app.services import sync_service as catalog
from app.services.catalog_errors import CatalogSyncBusyError
from app.services.discount_sync_service import DiscountSyncError, DiscountSyncService
from app.services.sync_activity import SYNC_EXECUTION_LOCK_KEY, mark_sync_execution, sync_execution_is_active


def service(engine, kind):
    factory = sessionmaker(bind=engine)
    if kind == "catalog":
        return catalog.SyncService(factory, expected_database=engine.url.database, store_id="test-store")
    return DiscountSyncService(factory, expected_database=engine.url.database,
                               store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12")


@pytest.mark.parametrize("kind", ["catalog", "discounts"])
@pytest.mark.parametrize("fail", [False, True])
def test_marker_lasts_for_transaction_and_releases_on_commit_or_rollback(database, kind, fail):
    runner = service(database, kind)
    with Session(bind=database) as observer:
        assert sync_execution_is_active(observer) is False
        observation_transaction = observer.get_transaction()
        with pytest.raises(RuntimeError, match="synthetic") if fail else nullcontext():
            with runner._transaction() as operator:
                assert sync_execution_is_active(observer) is True
                operator.execute(text('''INSERT INTO public."Products" (loyverse_id,name,synced_at)
                    VALUES ('marker-test','Prueba',:stamp)'''), {"stamp": datetime.now(timezone.utc)})
                if fail:
                    raise RuntimeError("synthetic")
        assert sync_execution_is_active(observer) is False
        assert observer.get_transaction() is observation_transaction and observation_transaction.is_active
    with database.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM public."Products"')) == (0 if fail else 1)


@pytest.mark.parametrize("first,second", [("catalog", "discounts"), ("discounts", "catalog")])
def test_services_still_exclude_each_other_and_do_not_clear_other_marker(database, first, second):
    with Session(bind=database) as observer:
        with service(database, first)._transaction():
            with pytest.raises((CatalogSyncBusyError, DiscountSyncError)):
                with service(database, second)._transaction():
                    pytest.fail("La segunda sincronización no debe iniciar")
            assert sync_execution_is_active(observer) is True
        assert sync_execution_is_active(observer) is False


@pytest.mark.parametrize("kind", ["catalog", "discounts"])
def test_marker_is_visible_before_provider_and_disappears_after_provider_error(
    database, kind, monkeypatch, commercial_snapshot,
):
    runner = service(database, kind)
    with Session(bind=database) as observer:
        async def provider(*args, **kwargs):
            assert sync_execution_is_active(observer) is True
            raise RuntimeError("synthetic-provider-failure")

        if kind == "catalog":
            monkeypatch.setattr(catalog, "fetch_catalog", AsyncMock(side_effect=provider))
            client = object()
        else:
            client = SimpleNamespace(get_discounts=AsyncMock(side_effect=provider))
        with pytest.raises(RuntimeError, match="synthetic-provider-failure"):
            asyncio.run(runner.synchronize(client))
        assert sync_execution_is_active(observer) is False


@pytest.mark.parametrize("kind", ["catalog", "discounts"])
def test_busy_marker_releases_new_commercial_lock_but_not_foreign_marker(database, kind):
    with database.begin() as owner:
        owner.scalar(select(func.pg_advisory_xact_lock(SYNC_EXECUTION_LOCK_KEY)))
        with pytest.raises((CatalogSyncBusyError, DiscountSyncError)):
            with service(database, kind)._transaction():
                pytest.fail("La marca ocupada debe rechazar la ejecución")
        with Session(bind=database) as observer:
            assert sync_execution_is_active(observer) is True
            assert observer.scalar(select(func.pg_try_advisory_xact_lock(catalog.CATALOG_SYNC_LOCK_KEY))) is True
    with Session(bind=database) as observer:
        assert sync_execution_is_active(observer) is False


def test_local_commercial_lock_is_not_reported_as_sync(database):
    with database.begin() as editor:
        editor.scalar(select(func.pg_advisory_xact_lock(catalog.CATALOG_SYNC_LOCK_KEY)))
        with Session(bind=database) as observer:
            assert sync_execution_is_active(observer) is False


def test_marker_in_other_database_is_ignored(database, sync_test_database):
    with sync_test_database.begin() as other:
        other.scalar(select(func.pg_advisory_xact_lock(SYNC_EXECUTION_LOCK_KEY)))
        with Session(bind=database) as observer:
            assert sync_execution_is_active(observer) is False


def test_two_integer_key_is_not_confused_with_bigint_key(database):
    with database.begin() as owner:
        owner.scalar(select(func.pg_advisory_xact_lock(
            SYNC_EXECUTION_LOCK_KEY >> 32, SYNC_EXECUTION_LOCK_KEY & 0xFFFFFFFF,
        )))
        with Session(bind=database) as observer:
            assert sync_execution_is_active(observer) is False


def test_emitting_marker_requires_an_existing_transaction():
    with Session() as db:
        with pytest.raises(ValueError, match="transacción activa"):
            mark_sync_execution(db)

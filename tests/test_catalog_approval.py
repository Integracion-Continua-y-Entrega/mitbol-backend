"""Autoriza exactamente el plan revisado; nunca abre PostgreSQL ni HTTP reales."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.cli import sync_catalog
from app.db.connection import CloudDatabaseTarget, DatabaseTargetError
from app.services.catalog_manifest import CatalogPlanChanged, catalog_manifest, manifest_sha256
from app.services.catalog_transform import transform_catalog
from app.services.sync_service import SyncService


def test_manifest_ignores_only_fetch_time_order_and_decimal_scale(commercial_snapshot):
    expected = catalog_manifest(transform_catalog(commercial_snapshot), commercial_snapshot.store)
    altered = deepcopy(commercial_snapshot)
    altered.items[0]["variants"][0]["stores"][0]["price"] = Decimal("89.000")
    altered = replace(altered, items=tuple(reversed(altered.items)),
                      fetched_at=altered.fetched_at + timedelta(seconds=1))
    actual = catalog_manifest(transform_catalog(altered), altered.store)
    assert actual == expected and manifest_sha256(actual) == manifest_sha256(expected)


@pytest.mark.parametrize("change", ["price", "availability", "name", "id", "assignment", "tombstone", "store_name"])
def test_same_counts_cannot_hide_catalog_changes(commercial_snapshot, change):
    expected = catalog_manifest(transform_catalog(commercial_snapshot), commercial_snapshot.store)
    altered = deepcopy(commercial_snapshot)
    item = altered.items[0]
    if change == "price":
        item["variants"][0]["stores"][0]["price"] = 90
    elif change == "availability":
        item["variants"][0]["stores"][0]["available_for_sale"] = False
    elif change == "name":
        item["item_name"] = "Nombre cambiado"
    elif change == "id":
        item["variants"][0]["variant_id"] = "new-identity"
    elif change == "assignment":
        item["modifier_ids"] = ["herbs"]
    elif change == "tombstone":
        altered = replace(altered, items=(*altered.items, {"id": "retired", "deleted_at": "2026-11-01T00:00:00Z"}))
    else:
        altered.store["name"] = "Otra denominación"
    target = MagicMock(spec=CloudDatabaseTarget)
    session = MagicMock()
    with pytest.raises(CatalogPlanChanged) as error:
        sync_catalog._initial_write_guard(target, {"catalog_manifest": expected})(
            session, altered, transform_catalog(altered),
        )
    assert error.value.differences
    session.connection.assert_not_called()
    target.validate_connection.assert_not_called()


@pytest.mark.parametrize("failure", ["none", "role", "readonly", "isolation", "rows", "revision", "target"])
def test_initial_gate_checks_actual_session_and_locks_before_last_count(commercial_snapshot, monkeypatch, failure):
    plan = transform_catalog(commercial_snapshot)
    approved = {"catalog_manifest": catalog_manifest(plan, commercial_snapshot.store), "user": "operator", "revision": "20261102_0003"}
    session = MagicMock()
    connection = session.connection.return_value
    target = MagicMock(spec=CloudDatabaseTarget)
    if failure == "target":
        target.validate_connection.side_effect = DatabaseTargetError("Destino incorrecto")
    revision = MagicMock(return_value="20261102_0003", side_effect=DatabaseTargetError("Revisión incorrecta") if failure == "revision" else None)
    monkeypatch.setattr(sync_catalog, "validate_catalog_revision", revision)
    monkeypatch.setattr(sync_catalog, "_inspect_operator", lambda _: {"user": "other" if failure == "role" else "operator"})
    def execute(sql):
        result = MagicMock()
        if sql == "SHOW transaction_isolation":
            result.scalar_one.return_value = "repeatable read" if failure == "isolation" else "read committed"
        else:
            result.scalar_one.return_value = "on" if failure == "readonly" else "off"
        return result
    connection.exec_driver_sql.side_effect = execute
    counts = MagicMock(return_value={"Products": 1 if failure == "rows" else 0})
    monkeypatch.setattr(sync_catalog, "_table_counts", counts)
    gate = sync_catalog._initial_write_guard(target, approved)
    if failure == "none":
        gate(session, commercial_snapshot, plan)
        sql = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
        assert sql[0] == "SHOW transaction_read_only"
        assert sql[1] == "SHOW transaction_isolation"
        assert sql[2].startswith('LOCK TABLE public."Products"') and sql[2].endswith("NOWAIT")
        assert all(f'public."{name}"' in sql[2] for name in sync_catalog.catalog_metadata.tables)
        counts.assert_called_once_with(connection)
    else:
        with pytest.raises(DatabaseTargetError):
            gate(session, commercial_snapshot, plan)


def test_gate_failure_precedes_all_persistence(commercial_snapshot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    gate = MagicMock(side_effect=DatabaseTargetError("No autorizado"))
    service = SyncService(MagicMock(), expected_database="mitbol_test_hu008_abcdef123456",
                          store_id="test-store", before_write=gate)
    write = MagicMock()
    monkeypatch.setattr(service, "_write", write)
    with pytest.raises(DatabaseTargetError):
        service._validated_write(MagicMock(), commercial_snapshot)
    gate.assert_called_once()
    write.assert_not_called()


def test_execute_without_saved_preflight_fails_before_connecting(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("ADMIN_ENABLED", "false")
    monkeypatch.setenv("EXPECTED_DATABASE", "mitbol_cloud_dev")
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", "ep-catalog.example.com")
    monkeypatch.setenv("DATABASE_URL", "postgresql://operator:PRIVATE@ep-catalog.example.com/mitbol_cloud_dev?sslmode=verify-full")
    monkeypatch.setenv("LOYVERSE_STORE_ID", "test-store")
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "PRIVATE_TOKEN")
    monkeypatch.delenv("MITBOL_SYNC_APPROVED_PREFLIGHT", raising=False)
    factory = MagicMock()
    monkeypatch.setattr(sync_catalog, "create_database_engine", factory)
    assert sync_catalog.main(["--execute", "--expected-database", "mitbol_cloud_dev",
                              "--expected-host", "ep-catalog.example.com", "--store-id", "test-store"]) == 1
    factory.assert_not_called()
    assert "PRIVATE" not in capsys.readouterr().out

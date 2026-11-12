"""Entrada periódica: una ejecución por disparo, sin jobs reales."""

from unittest.mock import AsyncMock, Mock

import pytest

from app.cli import run_sync as cli
from app.schemas.sync_requests import completed_response
from app.services.catalog_errors import CatalogSyncBusyError


@pytest.fixture
def runtime(monkeypatch):
    runtime = Mock()
    runtime.execute = AsyncMock(return_value=completed_response("discounts", 2))
    monkeypatch.setattr(cli.SyncWorkerConfig, "from_environment", Mock(return_value=object()))
    monkeypatch.setattr(cli, "SyncWorkerRuntime", Mock(return_value=runtime))
    return runtime


def test_default_only_preflights(runtime, capsys):
    assert cli.main(["--target", "discounts"]) == 0
    runtime.preflight.assert_called_once_with("discounts")
    runtime.execute.assert_not_awaited()
    assert '"writes": false' in capsys.readouterr().out
    runtime.close.assert_called_once()


def test_explicit_execute_runs_once(runtime, capsys):
    assert cli.main(["--target", "discounts", "--execute"]) == 0
    runtime.execute.assert_awaited_once_with("discounts")
    runtime.close.assert_called_once()


@pytest.mark.parametrize("skip,expected", [(False, 2), (True, 0)])
def test_busy_is_not_retried_and_scheduled_run_can_skip(runtime, capsys, skip, expected):
    runtime.execute.side_effect = CatalogSyncBusyError("busy")
    args = ["--target", "catalog", "--execute"] + (["--skip-if-busy"] if skip else [])
    assert cli.main(args) == expected
    runtime.execute.assert_awaited_once()
    runtime.close.assert_called_once()


def test_unexpected_errors_do_not_print_secrets(runtime, capsys):
    runtime.execute.side_effect = RuntimeError("SQL private-password")
    assert cli.main(["--target", "catalog", "--execute"]) == 1
    assert "private-password" not in capsys.readouterr().out

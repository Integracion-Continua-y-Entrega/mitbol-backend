"""CLI aislada: nunca abre una conexión ni consulta Loyverse real."""

import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.cli import sync_discounts as cli


STORE = "a3a56a06-d40c-4c07-931c-41abcbaaba12"
ARGS = ["--expected-database", "mitbol_dev", "--store-id", STORE]


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://operator@127.0.0.1:1/mitbol_dev")
    monkeypatch.setenv("LOYVERSE_STORE_ID", STORE)
    monkeypatch.delenv("LOYVERSE_ACCESS_TOKEN", raising=False)
    engine = Mock()
    monkeypatch.setattr(cli, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(cli, "preflight", Mock(return_value=dict(
        status="preflight_ok", writes=False, loyverse_contacted=False,
    )))
    monkeypatch.setattr(cli, "execute", AsyncMock(return_value=2))
    return engine


def test_default_preflight_needs_no_token_and_never_downloads(backend, capsys):
    assert cli.main(ARGS) == 0
    assert json.loads(capsys.readouterr().out)["writes"] is False
    cli.execute.assert_not_awaited()
    backend.dispose.assert_called_once()


def test_execute_requires_explicit_token_before_connection(backend, capsys):
    assert cli.main(ARGS + ["--execute"]) == 1
    assert "LOYVERSE_ACCESS_TOKEN" in capsys.readouterr().out
    cli.create_database_engine.assert_not_called()


def test_execute_uses_coordinator_and_does_not_print_token(backend, monkeypatch, capsys):
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "synthetic-private-token")
    assert cli.main(ARGS + ["--execute"]) == 0
    output = capsys.readouterr().out
    assert "synthetic-private-token" not in output
    assert json.loads(output)["processed"] == 2
    cli.execute.assert_awaited_once()
    backend.dispose.assert_called_once()


@pytest.mark.parametrize("value", ["", "invalid", "497f6eca-6276-4993-bfeb-53cbbbba6f08"])
def test_mismatched_store_is_rejected_before_connection(backend, monkeypatch, value, capsys):
    monkeypatch.setenv("LOYVERSE_STORE_ID", value)
    assert cli.main(ARGS) == 1
    cli.create_database_engine.assert_not_called()


def test_remote_url_is_rejected_in_local_mode(backend, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://operator:secret@remote.invalid/mitbol_dev")
    assert cli.main(ARGS) == 1
    assert "secret" not in capsys.readouterr().out
    cli.create_database_engine.assert_not_called()


def test_failed_preflight_never_downloads(backend, monkeypatch, capsys):
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "synthetic-private-token")
    cli.preflight.side_effect = cli.DiscountSyncError("Revisión incorrecta")
    assert cli.main(ARGS + ["--execute"]) == 1
    cli.execute.assert_not_awaited()
    backend.dispose.assert_called_once()


def test_driver_error_is_sanitized(backend, capsys):
    from sqlalchemy.exc import OperationalError
    cli.preflight.side_effect = OperationalError("SQL", {}, RuntimeError("private-password"))
    assert cli.main(ARGS) == 1
    assert "private-password" not in capsys.readouterr().out


def test_unexpected_error_is_sanitized(backend, capsys):
    cli.preflight.side_effect = RuntimeError("unexpected-private-password")
    assert cli.main(ARGS) == 1
    assert "unexpected-private-password" not in capsys.readouterr().out

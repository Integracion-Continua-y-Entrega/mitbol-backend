"""Protecciones de CI sin conexiones a PostgreSQL ni lectura de .env."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

import conftest


CI_URL = "postgresql+psycopg2://mitbol_ci:fake@127.0.0.1:5432/mitbol_test_ci"


@pytest.mark.parametrize("url", [
    None,
    "invalid-secret-url",
    CI_URL.replace("mitbol_test_ci", "mitbol_dev"),
    CI_URL.replace("127.0.0.1", "remote.example"),
    CI_URL.replace("mitbol_ci:", "real_user:"),
    CI_URL.replace("postgresql+psycopg2", "sqlite"),
    CI_URL + "?host=remote.example",
])
def test_ci_rejects_unapproved_database_without_exposing_url(monkeypatch, url):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    if url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(pytest.UsageError) as error:
        conftest._ci_database_url()
    assert "fake" not in str(error.value)
    assert "invalid-secret-url" not in str(error.value)


@pytest.mark.parametrize("flag", ["MITBOL_SYNC_POSTGRES_TESTS", "MITBOL_POSTGRES_TESTS"])
def test_ci_requires_both_postgresql_suites(monkeypatch, flag):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("DATABASE_URL", CI_URL)
    monkeypatch.setenv("MITBOL_SYNC_POSTGRES_TESTS", "1")
    monkeypatch.setenv("MITBOL_POSTGRES_TESTS", "1")
    monkeypatch.delenv(flag)
    with pytest.raises(pytest.UsageError, match="ambas suites"):
        conftest.pytest_configure(None)


def test_ci_fixture_never_reads_development_or_dotenv(monkeypatch):
    # Simular CI sin heredar la selección del cluster temporal de una corrida local.
    monkeypatch.delenv("MITBOL_SYNC_TEST_CLUSTER", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("DATABASE_URL", CI_URL)
    monkeypatch.setenv("MITBOL_SYNC_POSTGRES_TESTS", "1")

    def forbidden(*args, **kwargs):
        pytest.fail("CI intentó leer .env o consultar mitbol_dev")

    monkeypatch.setattr(conftest, "dotenv_values", forbidden)
    monkeypatch.setattr(conftest, "_development_state", forbidden)
    maintenance = MagicMock()
    connection = maintenance.connect.return_value.__enter__.return_value
    connection.scalar.return_value = "postgres"
    connection.dialect = postgresql.dialect()
    isolated = MagicMock()
    isolated.connect.return_value.__enter__.return_value.scalar.return_value = 0
    engines = MagicMock(side_effect=[maintenance, isolated])
    upgrade = MagicMock()
    monkeypatch.setattr(conftest, "create_engine", engines)
    monkeypatch.setattr(conftest.command, "upgrade", upgrade)

    fixture = conftest.sync_test_database.__wrapped__(MagicMock())
    assert next(fixture) is isolated
    fixture.close()
    urls = [call.args[0] for call in engines.call_args_list]
    assert urls[0].database == "postgres"
    assert urls[1].database.startswith("mitbol_test_hu008_")
    assert all(url.host == "127.0.0.1" for url in urls)
    upgrade.assert_called_once()
    # La regresión pública conserva el esquema de catálogo usado en producción;
    # autenticación migra el head en otra base temporal independiente.
    assert upgrade.call_args.args[1] == "20261102_0003"


@pytest.mark.parametrize("field,value", [
    ("server_version_num", 170000),
    ("current_user", "postgres"),
    ("session_user", "postgres"),
    ("server_address", None),
    ("server_address", "172.18.0.2"),
    ("server_address", "192.0.2.1"),
])
def test_auth_fixture_rejects_unauthorized_server_before_creating_database(monkeypatch, field, value):
    import test_auth_persistence as persistence

    monkeypatch.delenv("MITBOL_AUTH_TEST_CLUSTER", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("DATABASE_URL", CI_URL)
    target = dict(server_version_num=180000, database="postgres", current_user="mitbol_ci",
                  session_user="mitbol_ci", server_address="127.0.0.1", server_port=5432)
    target[field] = value
    engine = MagicMock()
    engine.dialect = postgresql.dialect()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one.return_value = target
    monkeypatch.setattr(persistence, "create_engine", MagicMock(return_value=engine))
    record = MagicMock()
    fixture = persistence.auth_database.__wrapped__(record)
    with pytest.raises(AssertionError):
        next(fixture)
    connection.exec_driver_sql.assert_not_called()
    record.assert_called_once()
    name, diagnostic = record.call_args.args
    assert name == "auth_control_target"
    assert "fake" not in diagnostic and "postgresql+psycopg2" not in diagnostic
    assert persistence.json.loads(diagnostic) == target

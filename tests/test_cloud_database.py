"""Destino, TLS efectivo y recuperación del pool; nunca se consulta Neon."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from alembic import command
from alembic.config import Config
from alembic.util import CommandError
import certifi
import pytest

from app.db.connection import (
    CloudDatabaseTarget, DatabaseTargetError, READ_ONLY_ROLE_SQL, connection_arguments,
    create_database_engine, local_database_url, postgresql_url, validate_local_connection,
)
from migrations.validation import validate_migration_target


HOST = "ep-catalog.example.com"
NAME = "mitbol_cloud_dev"
URL = f"postgresql+psycopg2://reader:PRIVATE_PASSWORD@{HOST}/{NAME}?sslmode=verify-full&channel_binding=require"


@pytest.fixture
def target():
    return CloudDatabaseTarget(NAME, HOST)


def driver_for(target):
    return SimpleNamespace(info=SimpleNamespace(
        host=target.expected_host, dbname=target.expected_database, port=5432, user="reader", ssl_in_use=True,
        dsn_parameters={"sslmode": "verify-full", "channel_binding": "require", "sslrootcert": certifi.where()},
    ))


@pytest.mark.parametrize("value", ["", "PRIVATE_SECRET", "sqlite:///private.db", "postgresql://host:wrong/db"])
def test_bad_urls_are_sanitized(value):
    with pytest.raises(DatabaseTargetError) as error:
        postgresql_url(value)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("database,host", [
    ("mitbol_dev", HOST), ("MITBOL_DEV", HOST), ("mitbol_test_hu008_abcdef123456", HOST),
    ("mitbol_test_ci", HOST), ("postgres", HOST), ("template0", HOST), ("", HOST),
    (NAME, "localhost"), (NAME, "127.0.0.1"), (NAME, "db.local"), (NAME, "db.invalid"),
    (NAME, "host:5432"), (NAME, "https://db.example.com"), (NAME, "db.example.com."),
    (NAME, "localhost.localdomain"),
])
def test_cloud_rejects_local_test_and_ambiguous_targets(database, host):
    with pytest.raises(DatabaseTargetError):
        CloudDatabaseTarget(database, host)


@pytest.mark.parametrize("value", [
    URL.replace(HOST, "other.example.com"), URL.replace(NAME, "mitbol_dev"),
    URL.replace("verify-full", "require"), URL.replace("verify-full", "disable"),
    URL.replace("postgresql+psycopg2", "postgresql+psycopg"),
    URL.replace("reader:PRIVATE_PASSWORD", "reader"),
    URL + "&host=other.example.com", URL + "&hostaddr=127.0.0.1", URL + "&dbname=other",
    URL + "&service=private", URL + "&options=-csearch_path=wrong", URL + "&port=5433",
    URL + "&sslmode=require", URL + "&channel_binding=disable",
    URL + "&sslrootcert=missing-private-certificate.pem",
])
def test_url_cannot_change_authorized_destination_or_tls(target, value):
    with pytest.raises(DatabaseTargetError) as error:
        target.validate_url(value)
    assert "PRIVATE_PASSWORD" not in str(error.value)


@pytest.mark.parametrize("variable", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"])
def test_libpq_environment_overrides_are_rejected_before_connecting(target, monkeypatch, variable):
    monkeypatch.setenv(variable, "PRIVATE_OVERRIDE")
    with pytest.raises(DatabaseTargetError) as error:
        create_database_engine(URL, target=target)
    assert "PRIVATE_OVERRIDE" not in str(error.value)


def test_cloud_connection_forces_certificate_hostname_and_channel_binding(target):
    url = target.validate_url(URL)
    arguments = connection_arguments(url, target)
    assert arguments["sslmode"] == "verify-full"
    assert arguments["sslrootcert"] == certifi.where()
    assert arguments["channel_binding"] == "require"
    assert arguments["gssencmode"] == "disable"
    assert arguments["ssl_min_protocol_version"] == "TLSv1.2"
    assert url.port == 5432


@pytest.mark.parametrize("attribute,value", [
    ("host", "other.example.com"), ("dbname", "mitbol_dev"), ("port", 55432),
    ("user", "owner"), ("ssl_in_use", False),
])
def test_driver_identity_is_verified_independently_of_url(target, attribute, value):
    driver = driver_for(target)
    setattr(driver.info, attribute, value)
    with pytest.raises(DatabaseTargetError):
        target.validate_driver(driver, target.validate_url(URL))


@pytest.mark.parametrize("key,value", [
    ("sslmode", "require"), ("channel_binding", "prefer"), ("hostaddr", "127.0.0.1"),
    ("service", "other"), ("options", "-c search_path=wrong"), ("sslrootcert", "/other-ca.pem"),
])
def test_effective_libpq_options_cannot_weaken_verification(target, key, value):
    driver = driver_for(target)
    driver.info.dsn_parameters[key] = value
    with pytest.raises(DatabaseTargetError):
        target.validate_driver(driver, target.validate_url(URL))


def test_current_database_is_checked_before_any_migration(target):
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    connection.engine.url = target.validate_url(URL)
    connection.connection.driver_connection = driver_for(target)
    connection.exec_driver_sql.return_value.scalar_one.return_value = "mitbol_dev"
    with pytest.raises(DatabaseTargetError, match="current_database"):
        validate_migration_target(connection, target)
    assert connection.exec_driver_sql.call_args.args[0] == "SELECT pg_catalog.current_database()"


@pytest.mark.parametrize("params", [(True, False), (False, True)])
def test_api_connection_rejects_wrong_database_and_writable_role(target, params):
    wrong_database, writable = params
    engine = create_database_engine(URL, target=target, read_only=True)
    driver = MagicMock()
    driver.info = driver_for(target).info
    cursor = driver.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [("other" if wrong_database else NAME,), (writable,)]
    try:
        # Ejecuta el callback real registrado, sin mocks de la política de seguridad.
        callbacks = [callback for callback in engine.pool.dispatch.connect
                     if getattr(callback, "__name__", "") == "verify_new_connection"]
        assert len(callbacks) == 1
        with pytest.raises(DatabaseTargetError):
            callbacks[0](driver, None)
        driver.rollback.assert_called_once()
        driver.set_session.assert_not_called()
    finally:
        engine.dispose()


def test_read_only_api_initializes_a_verified_connection_without_writes(target):
    engine = create_database_engine(URL, target=target, read_only=True)
    driver = MagicMock()
    driver.info = driver_for(target).info
    cursor = driver.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(NAME,), (False,)]
    try:
        callback = next(callback for callback in engine.pool.dispatch.connect
                        if getattr(callback, "__name__", "") == "verify_new_connection")
        callback(driver, None)
        assert [call.args[0] for call in cursor.execute.call_args_list] == [
            "SELECT pg_catalog.current_database()", READ_ONLY_ROLE_SQL,
        ]
        driver.set_session.assert_called_once_with(readonly=True)
    finally:
        engine.dispose()


def test_pool_replaces_a_closed_postgresql_connection(sync_test_database):
    engine = create_database_engine(sync_test_database.url)
    try:
        with engine.connect() as connection:
            old = connection.connection.driver_connection
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
        old.close()  # Simula que Neon cerró una conexión mientras estaba en el pool.
        with engine.connect() as connection:
            assert connection.connection.driver_connection is not old
            assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    finally:
        engine.dispose()


def test_role_privileges_query_detects_real_writer_and_reader(sync_db):
    connection = sync_db.connection
    assert connection.exec_driver_sql(READ_ONLY_ROLE_SQL).scalar_one() is True
    # CREATE ROLE y SET LOCAL se revierten con la transacción de esta prueba.
    name = "mitbol_reader_" + sync_db.engine.url.database.removeprefix("mitbol_test_hu008_")
    quoted = connection.dialect.identifier_preparer.quote(name)
    connection.exec_driver_sql(f"CREATE ROLE {quoted} NOLOGIN")
    connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {quoted}")
    connection.exec_driver_sql(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {quoted}")
    connection.exec_driver_sql(f"SET LOCAL ROLE {quoted}")
    try:
        assert connection.exec_driver_sql(READ_ONLY_ROLE_SQL).scalar_one() is False
        assert connection.exec_driver_sql('SELECT count(*) FROM "Products"').scalar_one() == 0
    finally:
        connection.exec_driver_sql("RESET ROLE")


def test_alembic_cloud_cannot_fallback_to_local_dotenv(monkeypatch):
    import dotenv

    def forbidden(*args, **kwargs):
        pytest.fail("Alembic cloud intentó leer .env")

    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("EXPECTED_DATABASE", NAME)
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", HOST)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(dotenv, "dotenv_values", forbidden)
    config = Config("alembic.ini")
    with pytest.raises(CommandError, match="DATABASE_URL"):
        command.current(config)


def test_supplied_alembic_connection_cannot_bypass_cloud_target(sync_db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("EXPECTED_DATABASE", NAME)
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", HOST)
    config = Config("alembic.ini")
    config.attributes["connection"] = sync_db.connection
    before = sync_db.connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    with pytest.raises(CommandError, match="destino cloud autorizado"):
        command.upgrade(config, "head")
    assert sync_db.connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == before


@pytest.mark.parametrize("value", [URL, "postgresql://u:p@localhost/db?host=remote.example.com"])
def test_omitting_cloud_mode_does_not_authorize_remote_operations(value):
    with pytest.raises(DatabaseTargetError, match="APP_ENV=cloud"):
        local_database_url(value)


def test_local_connection_cannot_hide_a_remote_driver_host():
    connection = MagicMock()
    connection.engine.url = postgresql_url("postgresql://u:p@localhost/mitbol_dev")
    connection.connection.driver_connection.info.host = HOST
    with pytest.raises(DatabaseTargetError, match="destino local"):
        validate_local_connection(connection)


def test_alembic_rejects_remote_url_without_cloud_authorization(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", URL)
    with pytest.raises(CommandError, match="APP_ENV=cloud"):
        command.current(Config("alembic.ini"))

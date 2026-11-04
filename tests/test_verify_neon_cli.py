"""Preflight previo a migraciones. No abre sockets ni consulta bases reales."""

import json
import os
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import psycopg2
import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.cli import verify_neon
from app.db.connection import CloudDatabaseTarget, DatabaseTargetError


HOST = "ep-example.us-east-2.aws.neon.tech"
NAME = "mitbol_cloud_dev"
URL = f"postgresql://neondb_owner:PRIVATE_PASSWORD@{HOST}/{NAME}?sslmode=verify-full&channel_binding=require"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("EXPECTED_DATABASE", NAME)
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", HOST)
    monkeypatch.setenv("DATABASE_URL", URL)
    for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"):
        monkeypatch.delenv(key, raising=False)


def test_validate_url_accepts_neon_scheme_without_connecting(configured, monkeypatch, capsys):
    from sqlalchemy.engine import Engine

    def forbidden(*args, **kwargs):
        pytest.fail("Validar la URL intentó abrir una conexión")

    monkeypatch.setattr(Engine, "connect", forbidden)
    assert verify_neon.main(["--validate-url"]) == 0
    output = capsys.readouterr().out
    assert '"status": "configuration_ok"' in output
    assert '"connection_checked": false' in output
    assert "PRIVATE_PASSWORD" not in output and "postgresql://" not in output


@pytest.mark.parametrize("name,value", [
    ("APP_ENV", "local"), ("APP_ENV", "production"), ("DATABASE_URL", ""),
    ("EXPECTED_DATABASE", "mitbol_dev"), ("EXPECTED_DATABASE", "mitbol_test_ci"),
    ("EXPECTED_DATABASE_HOST", "other.example.com"),
    ("DATABASE_URL", URL.replace("verify-full", "require")),
    ("DATABASE_URL", URL.replace("neondb_owner:", "reader:")),
    ("DATABASE_URL", URL + "&hostaddr=127.0.0.1"),
    ("DATABASE_URL", "PRIVATE_INVALID_URI"),
])
def test_rejects_unsafe_config_without_connecting(configured, monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    factory = MagicMock(side_effect=AssertionError("No debe crearse el motor"))
    monkeypatch.setattr(verify_neon, "create_database_engine", factory)
    assert verify_neon.main(["--validate-url"]) == 1
    factory.assert_not_called()
    assert "PRIVATE" not in capsys.readouterr().out


def test_pooled_host_is_rejected(configured, monkeypatch):
    host = HOST.replace("ep-example.", "ep-example-pooler.")
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", host)
    monkeypatch.setenv("DATABASE_URL", URL.replace(HOST, host))
    factory = MagicMock()
    monkeypatch.setattr(verify_neon, "create_database_engine", factory)
    assert verify_neon.main([]) == 1
    factory.assert_not_called()


def inspection_connection():
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execution_options.return_value = connection
    info = SimpleNamespace(
        host=HOST, port=5432, ssl_in_use=True,
        ssl_attribute=lambda key: "TLSv1.3",
        dsn_parameters={"sslmode": "verify-full", "channel_binding": "require"},
    )
    connection.connection.driver_connection.info = info

    def query(sql):
        result = MagicMock()
        if "current_user" in sql:
            result.one.return_value = (NAME, "neondb_owner", "neondb_owner", "on")
        elif sql == verify_neon.APPLICATION_OBJECTS_SQL:
            result.all.return_value = []
        return result

    connection.exec_driver_sql.side_effect = query
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = NAME
    return engine, connection, target


def test_inspection_is_read_only_rollback_and_accepts_no_revision(monkeypatch):
    engine, connection, target = inspection_connection()
    context = MagicMock()
    context.get_current_heads.return_value = ()
    monkeypatch.setattr(verify_neon.MigrationContext, "configure", lambda conn: context)
    report = verify_neon.inspect_database(engine, target, "neondb_owner")
    assert report["status"] == "ready_for_migrations"
    assert report["tls_active"] is True and report["tls_protocol"] == "TLSv1.3"
    assert report["read_only"] is True and report["writes"] is False
    assert report["alembic_heads"] == [] and report["application_objects"] == []
    connection.execution_options.assert_called_once_with(postgresql_readonly=True)
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert all(sql.lstrip().startswith(("SELECT", "SET TRANSACTION", "SET LOCAL")) for sql in statements)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    target.validate_connection.assert_called_once_with(connection)


@pytest.mark.parametrize("failure", ["identity", "tls", "revision", "objects", "driver"])
def test_inspection_stops_and_rolls_back_when_not_ready(monkeypatch, failure):
    engine, connection, target = inspection_connection()
    context = MagicMock()
    context.get_current_heads.return_value = ("20261102_0003",) if failure == "revision" else ()
    monkeypatch.setattr(verify_neon.MigrationContext, "configure", lambda conn: context)
    original = connection.exec_driver_sql.side_effect

    def query(sql):
        result = original(sql)
        if failure == "identity" and "current_user" in sql:
            result.one.return_value = ("mitbol_dev", "wrong", "wrong", "off")
        if failure == "objects" and sql == verify_neon.APPLICATION_OBJECTS_SQL:
            result.all.return_value = [("public", "Products", "r")]
        return result

    connection.exec_driver_sql.side_effect = query
    if failure == "tls":
        connection.connection.driver_connection.info.ssl_attribute = lambda key: "TLSv1.1"
    if failure == "driver":
        target.validate_connection.side_effect = DatabaseTargetError("Destino real incorrecto.")
    with pytest.raises(DatabaseTargetError):
        verify_neon.inspect_database(engine, target, "neondb_owner")
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()


def test_connection_failure_does_not_publish_driver_details(configured, monkeypatch, capsys):
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("PRIVATE_PASSWORD postgresql://PRIVATE_DSN PRIVATE_SQL")
    monkeypatch.setattr(verify_neon, "create_database_engine", MagicMock(return_value=engine))
    assert verify_neon.main([]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE" not in output and "postgresql://" not in output
    report = json.loads(output)
    assert report["stage"] == "postgresql_connect"
    assert report["code"] == "UNEXPECTED_CLIENT_ERROR"
    engine.dispose.assert_called_once()


def test_missing_cloud_url_never_loads_dotenv(configured, monkeypatch):
    import dotenv

    def forbidden(*args, **kwargs):
        pytest.fail("La verificación intentó leer .env")

    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setattr(dotenv, "dotenv_values", forbidden)
    assert verify_neon.main([]) == 1


@pytest.mark.parametrize("error,category,code", [
    (socket.gaierror(-2, "PRIVATE_HOST"), "dns", "DNS_RESOLUTION_FAILED"),
    (psycopg2.OperationalError('could not translate host name "PRIVATE_HOST"'), "dns", "DNS_RESOLUTION_FAILED"),
    (ConnectionRefusedError("PRIVATE_HOST"), "network", "CONNECTION_REFUSED"),
    (TimeoutError("PRIVATE_URI"), "network", "CONNECTION_TIMEOUT"),
    (psycopg2.OperationalError("timeout expired PRIVATE_URI"), "network", "CONNECTION_TIMEOUT"),
    (psycopg2.OperationalError("password authentication failed PRIVATE_PASSWORD"), "authentication", "AUTHENTICATION_FAILED"),
    (psycopg2.OperationalError('database "PRIVATE_DB" does not exist'), "postgresql", "DATABASE_NOT_FOUND"),
    (psycopg2.OperationalError("SSL error: certificate verify failed PRIVATE_CA"), "tls", "TLS_CERTIFICATE_VERIFICATION_FAILED"),
    (psycopg2.OperationalError('server certificate for "PRIVATE_HOST" does not match host name'), "tls", "TLS_HOSTNAME_MISMATCH"),
    (psycopg2.OperationalError("channel binding required PRIVATE_TOKEN"), "tls", "CHANNEL_BINDING_FAILED"),
    (psycopg2.OperationalError('invalid connection option "channel_binding" PRIVATE_VALUE'), "client", "UNSUPPORTED_CONNECTION_OPTION"),
    (psycopg2.OperationalError("SSL error: PRIVATE_DETAILS"), "tls", "TLS_NEGOTIATION_FAILED"),
    (psycopg2.OperationalError("SSL SYSCALL error: EOF detected PRIVATE"), "connection", "CONNECTION_CLOSED"),
    (psycopg2.InterfaceError("PRIVATE_ERROR"), "connection", "POSTGRESQL_CONNECTION_FAILED"),
    (UnicodeDecodeError("utf-8", b"PRIVATE_\xff", 8, 9, "PRIVATE_REASON"), "client", "CLIENT_ENCODING_ERROR"),
    (DatabaseTargetError("PRIVATE_URL"), "validation", "TARGET_VALIDATION_FAILED"),
])
def test_errors_are_classified_without_reflecting_secret_text(error, category, code):
    result = verify_neon.connection_diagnostic(error, "postgresql_connect")
    assert result["category"] == category and result["code"] == code
    assert "PRIVATE" not in json.dumps(result)


def test_real_driver_validation_code_is_reported_without_actual_values():
    error = DatabaseTargetError("PRIVATE_CONNECTION_DETAILS", code="TLS_CA_MISMATCH")
    report = verify_neon.connection_diagnostic(error, "postgresql_connect")
    assert report["code"] == "TLS_CA_MISMATCH"
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("error_class", [TypeError, AttributeError, KeyError, ValueError, NotImplementedError, RuntimeError])
def test_client_defects_identify_type_without_including_exception_arguments(error_class):
    report = verify_neon.connection_diagnostic(error_class("PRIVATE_PASSWORD PRIVATE_URI"), "identity_query")
    assert report["error_type"] == error_class.__name__
    assert report["code"] == "UNEXPECTED_CLIENT_ERROR"
    assert "PRIVATE" not in json.dumps(report)
    error = DatabaseTargetError("PRIVATE_MESSAGE", code="PRIVATE_CODE")
    report = verify_neon.connection_diagnostic(error, "postgresql_connect")
    assert report["code"] == "TARGET_VALIDATION_FAILED"
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("sqlstate,code", [
    ("28P01", "AUTHENTICATION_FAILED"), ("28000", "AUTHENTICATION_FAILED"),
    ("3D000", "DATABASE_NOT_FOUND"), ("42501", "INSUFFICIENT_PRIVILEGE"),
    ("25006", "READ_ONLY_VIOLATION"), ("42883", "POSTGRESQL_QUERY_FAILED"),
])
def test_wrapped_postgresql_error_exposes_only_sqlstate(sqlstate, code):
    class DriverError(psycopg2.ProgrammingError):
        @property
        def pgcode(self):
            return sqlstate

    original = DriverError("PRIVATE_PASSWORD PRIVATE_SERVER_ERROR")
    wrapped = ProgrammingError("PRIVATE_SQL", {"password": "PRIVATE_PASSWORD"}, original)
    report = verify_neon.connection_diagnostic(wrapped, "identity_query")
    assert report["sqlstate"] == sqlstate and report["code"] == code
    assert report["stage"] == "identity_query"
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("stage,error,expected_code", [
    ("dns", socket.gaierror(-2, "PRIVATE_HOST"), "DNS_RESOLUTION_FAILED"),
    ("tcp", TimeoutError("PRIVATE_ADDR"), "CONNECTION_TIMEOUT"),
    ("tcp", OSError("PRIVATE_NETWORK"), "NETWORK_UNREACHABLE"),
])
def test_diagnostic_network_probe_never_reaches_postgresql_on_failure(
    configured, monkeypatch, capsys, stage, error, expected_code,
):
    engine = MagicMock()
    monkeypatch.setattr(verify_neon, "create_database_engine", MagicMock(return_value=engine))
    dns = MagicMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 5432))])
    tcp = MagicMock()
    (dns if stage == "dns" else tcp).side_effect = error
    monkeypatch.setattr(verify_neon.socket, "getaddrinfo", dns)
    monkeypatch.setattr(verify_neon.socket, "create_connection", tcp)
    assert verify_neon.main(["--diagnose"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == stage and report["code"] == expected_code
    assert report["checks_passed"] == (["configuration"] if stage == "dns" else ["configuration", "dns"])
    assert "PRIVATE" not in json.dumps(report)
    assert "libpq" in report["runtime"]
    engine.connect.assert_not_called()


def test_handshake_failure_after_successful_probes_is_reported(configured, monkeypatch, capsys):
    engine = MagicMock()
    original = psycopg2.OperationalError("SSL error: certificate verify failed PRIVATE_CA PRIVATE_PASSWORD")
    engine.connect.side_effect = OperationalError("PRIVATE_SQL", {}, original)
    monkeypatch.setattr(verify_neon, "create_database_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(verify_neon.socket, "getaddrinfo", MagicMock(return_value=[True]))
    monkeypatch.setattr(verify_neon.socket, "create_connection", MagicMock())
    assert verify_neon.main(["--diagnose"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == "postgresql_connect"
    assert report["code"] == "TLS_CERTIFICATE_VERIFICATION_FAILED"
    assert report["checks_passed"] == ["configuration", "dns", "tcp"]
    assert "PRIVATE" not in json.dumps(report)


def test_readonly_setup_failure_closes_the_acquired_connection():
    engine, connection, target = inspection_connection()
    connection.execution_options.side_effect = RuntimeError("PRIVATE_READONLY_ERROR")
    diagnostics = {"checks_passed": []}
    with pytest.raises(RuntimeError):
        verify_neon.inspect_database(engine, target, "neondb_owner", diagnostics=diagnostics)
    assert diagnostics["stage"] == "readonly_setup"
    engine.connect.return_value.__exit__.assert_called_once()
    connection.rollback.assert_called_once()
    connection.exec_driver_sql.assert_not_called()


def test_query_failure_preserves_stage_and_cannot_leak_sql(configured, monkeypatch, capsys):
    engine, connection, target = inspection_connection()
    connection.exec_driver_sql.side_effect = psycopg2.ProgrammingError("PRIVATE_SQL PRIVATE_PASSWORD")
    monkeypatch.setattr(verify_neon, "create_database_engine", MagicMock(return_value=engine))
    assert verify_neon.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == "readonly_setup"
    assert "PRIVATE" not in json.dumps(report)


def test_validate_url_diagnose_never_uses_dns_or_tcp(configured, monkeypatch, capsys):
    dns = MagicMock(side_effect=AssertionError("No debe resolver DNS"))
    tcp = MagicMock(side_effect=AssertionError("No debe abrir TCP"))
    monkeypatch.setattr(verify_neon.socket, "getaddrinfo", dns)
    monkeypatch.setattr(verify_neon.socket, "create_connection", tcp)
    before = dict(os.environ)
    assert verify_neon.main(["--validate-url", "--diagnose"]) == 0
    assert dict(os.environ) == before
    dns.assert_not_called()
    tcp.assert_not_called()
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "configuration_ok" and report["connection_checked"] is False


def test_rollback_failure_does_not_hide_original_validation_error():
    engine, connection, target = inspection_connection()
    target.validate_connection.side_effect = DatabaseTargetError("PRIVATE_TARGET_ERROR")
    connection.rollback.side_effect = psycopg2.OperationalError("PRIVATE_ROLLBACK_ERROR")
    diagnostics = {"checks_passed": []}
    with pytest.raises(DatabaseTargetError):
        verify_neon.inspect_database(engine, target, "neondb_owner", diagnostics=diagnostics)
    assert diagnostics["stage"] == "target_validation"
    assert diagnostics["rollback_failed"] is True

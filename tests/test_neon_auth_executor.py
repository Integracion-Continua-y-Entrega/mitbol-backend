"""Diagnóstico sin secretos y ejecutor documentado, sólo mocks/cluster temporal."""

import json
import logging
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

from alembic import op
from alembic.util import CommandError
import psycopg2
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db import connection as policy
from scripts import diagnose_neon_auth_migration as diagnostic
from test_neon_auth_migration_preflight import catalog_snapshot, migration_lab


ROOT = Path(__file__).resolve().parents[1]
CORE = "20261105_0004"
SECRET = "PRIVATE_PASSWORD postgresql://PRIVATE_DSN PRIVATE_SQL"


@pytest.fixture(autouse=True)
def preserve_logging():
    # exec() reproduce el proceso documentado sin contaminar otros tests.
    previous = logging.root.manager.disable
    yield
    logging.disable(previous)


class PermissionErrorForTest(psycopg2.ProgrammingError):
    @property
    def pgcode(self):
        return "42501"


def wrapped_error():
    try:
        try:
            raise ProgrammingError(SECRET, {"secret": SECRET}, PermissionErrorForTest(SECRET))
        except ProgrammingError:
            raise CommandError(SECRET) from None
    except CommandError as error:
        return error


def test_nested_alembic_error_retains_sqlstate_without_any_secret():
    report = diagnostic.failure_report(wrapped_error(), "alembic_upgrade")
    assert report["stage"] == "alembic_upgrade"
    assert report["code"] == "INSUFFICIENT_PRIVILEGE"
    assert report["sqlstate"] == "42501"
    assert report["error_type"] == "ProgrammingError"
    assert "PRIVATE" not in json.dumps(report)


def test_alembic_error_without_database_cause_is_classified_without_message():
    report = diagnostic.failure_report(CommandError(SECRET), "alembic_graph")
    assert report["error_type"] == "CommandError" and report["code"] == "ALEMBIC_COMMAND_FAILED"
    assert report["sqlstate"] is None and "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("error,code", [
    (ImportError(SECRET), "DEPENDENCY_IMPORT_FAILED"),
    (policy.DatabaseTargetError(SECRET, code="TLS_NOT_ACTIVE"), "TLS_NOT_ACTIVE"),
    (psycopg2.OperationalError("channel binding required " + SECRET), "CHANNEL_BINDING_FAILED"),
    (RuntimeError(SECRET), "UNEXPECTED_CLIENT_ERROR"),
])
def test_error_messages_never_escape(error, code):
    report = diagnostic.failure_report(error, "connect", ("imports", SECRET))
    assert report["code"] == code
    assert report["checks_passed"] == ["imports"]
    assert "PRIVATE" not in json.dumps(report)


def test_parser_never_reflects_unexpected_arguments(capsys):
    assert diagnostic.main(["--revision", SECRET]) == 1
    output = capsys.readouterr()
    assert not output.err and "PRIVATE" not in output.out
    assert json.loads(output.out)["code"] == "ARGUMENTS_REJECTED"


@pytest.mark.parametrize("failure,stage", [("connect", "connect"), ("permissions", "permissions"), ("revision", "revision_before")])
def test_readonly_mode_fails_closed_and_never_runs_alembic_upgrade(monkeypatch, capsys, failure, stage):
    from alembic import command
    from migrations import validation

    forbidden = MagicMock(side_effect=AssertionError("DDL forbidden"))
    monkeypatch.setattr(command, "upgrade", forbidden)
    target = MagicMock(expected_database="mitbol_cloud_dev", expected_host=diagnostic.EXPECTED_HOST)
    target.validate_url.return_value = SimpleNamespace(username="neondb_owner", host=diagnostic.EXPECTED_HOST)
    monkeypatch.setattr(policy, "cloud_target_from_environment", lambda: target)
    monkeypatch.setenv("DATABASE_URL", SECRET)
    engine, db = MagicMock(), MagicMock()
    engine.connect.return_value.__enter__.return_value = db
    if failure == "connect":
        engine.connect.side_effect = psycopg2.OperationalError("channel binding " + SECRET)

    def query(sql):
        result = MagicMock()
        if sql == "SELECT current_user,session_user":
            result.one.return_value = ("neondb_owner",) * 2
        elif sql == "SHOW server_version_num":
            result.scalar_one.return_value = "180006"
        elif sql == "SHOW transaction_read_only":
            result.scalar_one.return_value = "on"
        elif sql == "SHOW default_transaction_read_only":
            result.scalar_one.return_value = "off"
        elif sql == "SELECT current_schema()":
            result.scalar_one.return_value = "public"
        elif sql.startswith("SELECT has_schema_privilege"):
            result.scalar_one.return_value = failure != "permissions"
        elif sql == "SELECT version_num FROM public.alembic_version":
            result.scalars.return_value.all.return_value = [CORE] if failure == "revision" else [diagnostic.PREVIOUS[CORE]]
        return result

    db.exec_driver_sql.side_effect = query
    monkeypatch.setattr(policy, "create_database_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(validation, "validate_initial_database", MagicMock())
    assert diagnostic.main(["--revision", CORE]) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["stage"] == stage
    assert "PRIVATE" not in output.out + output.err
    forbidden.assert_not_called()
    db.commit.assert_not_called()
    engine.dispose.assert_called_once()
    if failure != "connect":
        assert db.exec_driver_sql.call_args_list[0].args[0] == "SET TRANSACTION READ ONLY"
        db.execution_options.assert_called_once_with(postgresql_readonly=True)


def documented_python():
    document = (ROOT / "docs/security/neon_auth_migration_preflight.md").read_text(encoding="utf-8")
    section = document.split("### 6.3 ", 1)[1].split("### 6.4 ", 1)[0]
    return re.search(r"@'\n(.*?)\n'@", section, re.S).group(1)


def test_readonly_source_has_no_migration_or_catalog_operations():
    source = Path(diagnostic.__file__).read_text(encoding="utf-8")
    assert not re.search(r"command\.(upgrade|downgrade|stamp)\s*\(", source)
    for table in ("Products", "ProductVariants", "ProductComplements", "PersonalAccounts"):
        assert table not in source
    assert "SET TRANSACTION READ ONLY" in source and "db.rollback()" in source


@pytest.mark.parametrize("change", ["environment", "database", "host", "operator", "tls"])
def test_unapproved_configuration_is_rejected_before_connection(monkeypatch, capsys, change):
    monkeypatch.setenv("APP_ENV", "local" if change == "environment" else "cloud")
    monkeypatch.setenv("EXPECTED_DATABASE", "mitbol_cloud_dev")
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", diagnostic.EXPECTED_HOST)
    database = "mitbol_dev" if change == "database" else "mitbol_cloud_dev"
    host = "127.0.0.1" if change == "host" else diagnostic.EXPECTED_HOST
    user = "mitbol_render_reader" if change == "operator" else "neondb_owner"
    tls = "require" if change == "tls" else "verify-full"
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg2://{user}:fake@{host}/{database}?sslmode={tls}&channel_binding=require")
    forbidden = MagicMock(side_effect=AssertionError("No connection allowed"))
    monkeypatch.setattr(policy, "create_database_engine", forbidden)
    assert diagnostic.main(["--revision", CORE]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == "configuration"
    forbidden.assert_not_called()


def test_diagnostic_and_documented_executor_in_isolated_postgres(migration_lab, monkeypatch, capsys):
    """El adaptador de prueba sustituye sólo TLS/destino cloud; nunca existe en CLI."""
    lab = migration_lab
    assert re.fullmatch(r"mitbol_test_auth_[0-9a-f]{32}", lab.owner.url.database)
    assert lab.owner.url.host == "127.0.0.1" and lab.owner.url.username == "mitbol_ci"
    baseline = catalog_snapshot(lab.owner)
    with lab.owner.begin() as db:
        assert int(db.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 18
        db.exec_driver_sql("CREATE ROLE neondb_owner LOGIN PASSWORD 'mitbol-diagnostic-fixture'")
        lab.created_roles.append("neondb_owner")
        db.exec_driver_sql("GRANT USAGE,CREATE ON SCHEMA public TO neondb_owner")
        db.exec_driver_sql("GRANT SELECT,UPDATE ON public.alembic_version TO neondb_owner")
    local = create_engine(lab.owner.url.set(username="neondb_owner", password="mitbol-diagnostic-fixture"), hide_parameters=True)
    lab.engines.append(local)
    approved = policy.CloudDatabaseTarget("mitbol_cloud_dev", diagnostic.EXPECTED_HOST)

    def validate_local(db):
        policy.validate_local_connection(db)
        assert db.engine.url.database == lab.owner.url.database
        assert db.connection.driver_connection.info.user == "neondb_owner"

    adapter = SimpleNamespace(expected_database=approved.expected_database, expected_host=approved.expected_host,
                              validate_url=approved.validate_url, validate_connection=validate_local)

    def isolated_engine(url, **kwargs):
        assert url.host == diagnostic.EXPECTED_HOST and url.database == "mitbol_cloud_dev"
        assert kwargs == {"target": adapter, "read_only": False}
        return local

    monkeypatch.setattr(policy, "cloud_target_from_environment", lambda: adapter)
    monkeypatch.setattr(policy, "create_database_engine", isolated_engine)
    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg2://neondb_owner:fake@{diagnostic.EXPECTED_HOST}/mitbol_cloud_dev?sslmode=verify-full&channel_binding=require")
    assert diagnostic.main(["--revision", CORE]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inspected_readonly" and report["ddl_executed"] is False
    assert catalog_snapshot(lab.owner) == baseline
    with local.connect() as db:
        db.exec_driver_sql("SET TRANSACTION READ ONLY")
        with pytest.raises(DBAPIError) as caught:
            db.exec_driver_sql('CREATE TABLE "ForbiddenDiagnosticWrite" (id int)')
        assert caught.value.orig.pgcode == "25006"

    monkeypatch.setattr("sys.argv", ["-", CORE])
    code = compile(documented_python(), "<documented-executor>", "exec")
    with monkeypatch.context() as patch:
        patch.setattr(op, "create_index", lambda *args, **kwargs: (_ for _ in ()).throw(
            ProgrammingError(SECRET, {}, PermissionErrorForTest(SECRET))))
        with pytest.raises(SystemExit) as stopped:
            exec(code, {})
        assert stopped.value.code == 1
    output = capsys.readouterr()
    failure = json.loads(output.out)
    assert failure["stage"] == "alembic_upgrade" and failure["sqlstate"] == "42501"
    assert "PRIVATE" not in output.out + output.err
    with lab.owner.connect() as db:
        assert db.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one() == diagnostic.PREVIOUS[CORE]
        assert set(inspect(db).get_table_names()) == set(baseline[0]) | {"alembic_version"}
    assert catalog_snapshot(lab.owner) == baseline

    exec(code, {})
    assert json.loads(capsys.readouterr().out) == {"status": "committed", "revision": CORE}
    with lab.owner.connect() as db:
        assert db.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar_one() == CORE
        assert db.exec_driver_sql('SELECT code FROM "Roles" ORDER BY code').scalars().all() == ["admin", "staff"]
        assert len(set(inspect(db).get_table_names()) - set(baseline[0]) - {"alembic_version"}) == 7
        assert db.exec_driver_sql('SELECT to_regclass(\'public."AuthRateLimits"\')').scalar_one() is None
        for table in ("Persons", "PersonalAccounts", "Employees", "EmployeeRoles", "AccountSessions", "AuditEvents"):
            assert db.exec_driver_sql(f'SELECT count(*) FROM "{table}"').scalar_one() == 0
    assert catalog_snapshot(lab.owner) == baseline

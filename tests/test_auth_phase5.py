"""Preparación local: límites PostgreSQL, gates y CORS por ámbito."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from secrets import token_urlsafe

from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import OperationalError

from test_auth_persistence import auth_database, config_for
from test_auth_api import backend as auth_backend, password_hash, login, csrf_headers
from test_auth_admin_api import backend as catalog_backend
from test_admin_writer import backend as writer_backend, writer_database, denied
from test_admin_writer import execute_script, ROOT
from test_auth_phase2_core import config as local_config
from app.core.auth_errors import AuthError
from app.core.auth_http import cookie_name
from app.core.security import AdminRequestGuard
from app.main import create_app
from app.models import AccountSession, AuditEvent, AuthRateLimit, AuthRateLimitPolicy
from app.services.auth_service import AuthService


@pytest.fixture
def backend(writer_backend):
    b = writer_backend
    with b.sessions.begin() as db:
        db.execute(delete(AuthRateLimit))
        db.execute(delete(AuthRateLimitPolicy))
    b.config = b.config.model_copy(update={"auth_limit_backend": "postgresql", "auth_limit_secret": SecretStr(token_urlsafe(32)),
        "auth_login_attempts": 5, "auth_ip_attempts": 30})
    b.config.validate_auth_configuration()
    b.app.state.settings = b.config
    b.service = AuthService(b.app.state.auth_session_factory, b.config, clock=lambda: b.now.value)
    b.app.state.auth_service = b.service
    yield b
    with b.sessions.begin() as db:
        db.execute(delete(AuthRateLimit))
        db.execute(delete(AuthRateLimitPolicy))


def test_migration_limits_is_incremental_and_offline():
    output = StringIO()
    command.upgrade(config_for(output=output), "20261105_0004:20261106_0005", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 2 and 'CREATE TABLE "AuthRateLimits"' in sql
    assert "ALTER TABLE" not in sql and "GRANT " not in sql and "CREATE ROLE" not in sql


def test_persistent_limits_survive_service_replacement_and_use_no_clear_identifiers(backend):
    b = backend
    for _ in range(5):
        assert login(b, password="wrong").status_code == 401
        b.app.state.auth_service = AuthService(b.app.state.auth_session_factory, b.config)
    response = login(b)
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
    assert "Retry-After" in response.headers["access-control-expose-headers"]
    with b.sessions() as db:
        rows = list(db.scalars(select(AuthRateLimit)))
        assert len(rows) == 2 and {row.attempts for row in rows} == {5}
        assert b.emails["admin"] not in repr([(r.key_hash, r.attempts) for r in rows])
        assert db.scalar(select(func.count()).select_from(AccountSession)) == 0


def test_concurrent_independent_limiters_never_exceed_limit(backend):
    b = backend
    other = AuthService(b.app.state.auth_session_factory, b.config)
    def attempt(index):
        try:
            (b.service if index % 2 else other).limiter.admit(b.emails["admin"], "127.0.0.1")
            return True
        except AuthError as error:
            assert error.status_code == 429
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = sum(pool.map(attempt, range(20)))
    assert 1 <= admitted <= 5
    for _ in range(5 - admitted):
        assert attempt(0)
    assert not attempt(0)
    with b.sessions() as db:
        assert {row.attempts for row in db.scalars(select(AuthRateLimit))} == {5}


@pytest.mark.parametrize("change", ["secret", "limit", "window", "capacity"])
def test_configuration_drift_is_rejected_instead_of_resetting_counters(backend, change):
    b = backend
    b.service.limiter.admit(b.emails["admin"], "127.0.0.1")
    values = {"secret": {"auth_limit_secret": SecretStr(token_urlsafe(32))}, "limit": {"auth_login_attempts": 6},
              "window": {"auth_limit_window_seconds": 1000}, "capacity": {"auth_limit_max_keys": 3000}}[change]
    other = AuthService(b.app.state.auth_session_factory, b.config.model_copy(update=values))
    with pytest.raises(AuthError) as error:
        other.limiter.admit(b.emails["admin"], "127.0.0.1")
    assert error.value.status_code == 503


def test_database_clock_expiry_and_namespace_separation(backend):
    b = backend
    for _ in range(5):
        b.service.limiter.admit(b.emails["admin"], "127.0.0.1")
    b.now.value += timedelta(days=1)
    with pytest.raises(AuthError):
        b.service.limiter.admit(b.emails["admin"], "127.0.0.1")
    b.service.limiter.admit(b.emails["admin"], "127.0.0.1", operation="change_password")
    with b.sessions.begin() as db:
        db.execute(text('UPDATE "AuthRateLimits" SET started_at=now()-interval \'1 hour\', expires_at=now()-interval \'1 second\''))
    b.service.limiter.admit(b.emails["admin"], "127.0.0.1")
    with b.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuthRateLimit)) == 2
        assert {r.attempts for r in db.scalars(select(AuthRateLimit))} == {1}


def test_normalization_ip_rotation_and_ipv6_prefix_cannot_evade_limits(backend):
    b = backend
    for index in range(5):
        b.service.limiter.admit("  " + b.emails["admin"].upper() + "  ", f"127.0.0.{index + 1}")
    with pytest.raises(AuthError):
        b.service.limiter.admit(b.emails["admin"], "127.0.0.99")
    # Una red IPv6 /64 comparte el límite aunque cambie la dirección de interfaz.
    b.service.limiter.ip_limit = 1
    b.service.limiter.admit("one@example.test", "2001:db8::1")
    with pytest.raises(AuthError):
        b.service.limiter.admit("two@example.test", "2001:db8::2")


def test_capacity_is_bounded_and_does_not_evict_live_entries(backend):
    b = backend
    b.service.limiter.max_keys = 2
    b.service.limiter.admit("one@example.test", "127.0.0.1")
    with pytest.raises(AuthError):
        b.service.limiter.admit("two@example.test", "127.0.0.1")
    with b.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuthRateLimit)) == 2


def test_login_failure_rollback_does_not_reset_admitted_attempt(backend):
    b = backend
    def fail(mapper, connection, target):
        raise OperationalError("synthetic SQL", {}, Exception("synthetic"))
    event.listen(AuditEvent, "before_insert", fail)
    try:
        assert login(b).status_code == 503
    finally:
        event.remove(AuditEvent, "before_insert", fail)
    with b.sessions() as db:
        assert {r.attempts for r in db.scalars(select(AuthRateLimit))} == {1}
        assert db.scalar(select(func.count()).select_from(AccountSession)) == 0


def test_storage_failure_fails_closed_without_breaking_public_menu(backend):
    b = backend
    with b.sessions.begin() as db:
        db.execute(text('REVOKE SELECT(key_hash) ON "AuthRateLimits" FROM mitbol_admin_writer'))
    try:
        assert login(b).status_code == 503
        assert b.client.get("/api/menu").status_code == 200
    finally:
        with b.sessions.begin() as db:
            db.execute(text('GRANT SELECT(key_hash) ON "AuthRateLimits" TO mitbol_admin_writer'))


@pytest.mark.parametrize("table", ["AuthRateLimits", "AuthRateLimitPolicy"])
def test_reader_cannot_read_persistent_limits(writer_database, table):
    denied(writer_database.reader, f'SELECT * FROM "{table}"')


@pytest.mark.parametrize("sql", ['DELETE FROM "AuthRateLimitPolicy"',
    'UPDATE "AuthRateLimitPolicy" SET fingerprint = repeat(\'a\',64)',
    'UPDATE "AuthRateLimits" SET expires_at = now()', 'TRUNCATE "AuthRateLimits"'])
def test_writer_cannot_reset_policy_or_extend_windows(writer_database, sql):
    denied(writer_database.writer, sql)


@pytest.mark.parametrize("secret", [None, "short", "a" * 44])
def test_postgres_backend_requires_canonical_shared_secret(secret):
    with pytest.raises(ValidationError):
        local_config(auth_limit_backend="postgresql", auth_limit_secret=secret)


def test_multiple_workers_only_with_persistent_backend(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert local_config(auth_limit_backend="postgresql", auth_limit_secret=token_urlsafe(32)).auth_enabled


@pytest.mark.parametrize("catalog,images", [(False, False), (True, False)])
def test_gradual_registration_keeps_image_writes_closed(backend, catalog, images):
    b = backend
    config = b.config.model_copy(update={"auth_catalog_enabled": catalog, "auth_images_enabled": images})
    app = create_app(config)
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54350)) as client:
        schema = client.get("/openapi.json").json()["paths"]
        assert "/api/auth/login" in schema
        assert ("/api/admin/tags" in schema) is catalog
        assert "post" not in schema.get("/api/admin/variants/{variant_id}/images", {})
        assert client.post("/api/admin/variants/1/images", headers={"Origin": "http://localhost:5173"}, content=b"invalid").status_code == 404


def test_public_cors_keeps_local_react_independent_from_admin_origins(backend):
    b = backend
    config = b.config.model_copy(update={"auth_origins": ["http://localhost:3000"],
        "cors_origins": ["http://localhost:5173"]})
    app = create_app(config)
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54351)) as client:
        response = client.get("/api/menu", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 200 and response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "access-control-allow-credentials" not in response.headers
        response = client.get("/api/auth/me", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 403 and "access-control-allow-origin" not in response.headers


def test_local_stage_options_cannot_bypass_cloud_block(backend):
    from test_cloud_configuration import cloud_settings
    cloud = cloud_settings()
    for updates in ({"auth_enabled": True, "auth_limit_backend": "postgresql",
                     "auth_limit_secret": SecretStr(token_urlsafe(32))},
                    {"auth_enabled": True, "auth_catalog_enabled": True, "auth_images_enabled": True}):
        with pytest.raises(ValueError, match="bloqueado"):
            create_app(cloud.model_copy(update=updates))


def test_staff_image_upload_rejected_before_any_body_is_received(backend):
    b = backend
    headers = csrf_headers(login(b, "staff"))
    received = []
    async def forbidden_receive():
        pytest.fail("Se recibió el cuerpo multipart antes de comprobar permiso de Staff")
    async def forbidden_app(scope, receive, send):
        pytest.fail("Se alcanzó el parser sin permiso")
    async def send(message):
        received.append(message)
    scope = {"type": "http", "method": "POST", "path": f"/api/admin/variants/{b.variant_id}/images",
        "root_path": "", "query_string": b"", "scheme": "http", "http_version": "1.1", "app": b.app,
        "client": ("127.0.0.1", 54353), "server": ("localhost", 8000),
        "headers": [(b"host", b"localhost:8000"), (b"origin", headers["Origin"].encode()),
            (b"x-csrf-token", headers["X-CSRF-Token"].encode()), (b"content-type", b"multipart/form-data; boundary=test"),
            (b"cookie", f'{cookie_name(b.config)}={b.client.cookies.get(cookie_name(b.config))}'.encode())]}
    asyncio.run(AdminRequestGuard(forbidden_app, b.config)(scope, forbidden_receive, send))
    assert received[0]["status"] == 403


def test_production_sql_is_generated_from_tested_acl_and_rejects_local_execution(writer_database):
    import re
    from scripts.render_admin_deployment_sql import render_prepare, render_verify
    from sqlalchemy.exc import DBAPIError
    local = (ROOT / "docs/database/prepare_admin_writer_local.sql").read_text(encoding="utf-8")
    prepared = (ROOT / "docs/database/prepare_admin_writer_neon.sql").read_text(encoding="utf-8")
    assert prepared == render_prepare()
    assert (ROOT / "docs/database/verify_admin_writer_neon.sql").read_text(encoding="utf-8") == render_verify()
    assert re.findall(r"GRANT\b[^;]+;", prepared) == re.findall(r"GRANT\b[^;]+;", local)
    with pytest.raises(DBAPIError, match="NEON_WRITER_NOT_APPROVED"):
        execute_script(writer_database.owner, prepared)


def test_neon_writer_scripts_with_proxy_tls_marker_on_isolated_copy(writer_database):
    """Sólo la copia en memoria cambia destino/roles; no hay conexión a Neon."""
    import re
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    from scripts.render_admin_deployment_sql import render_prepare, render_verify

    r = writer_database
    assert re.fullmatch(r"mitbol_test_auth_[a-f0-9]{32}", r.owner.url.database)
    assert r.owner.url.host == "127.0.0.1" and r.owner.url.username == "mitbol_ci"
    name = "mitbol_neon_writer_fixture"
    prepare, verify = render_prepare(), render_verify()
    assert "pg_stat_ssl" not in prepare + verify
    assert "external_tls_verified" in prepare and "external_tls_verified" in verify
    prepare = prepare.replace("mitbol_cloud_dev", r.owner.url.database).replace("neondb_owner", "mitbol_ci").replace("mitbol_admin_writer", name)
    verify = verify.replace("mitbol_cloud_dev", r.owner.url.database).replace("mitbol_admin_writer", name)
    tls = "SET LOCAL mitbol.external_tls_verified='verify-full-channel-binding';\n"
    approved = "SET LOCAL mitbol.approved_admin_write='phase5-reviewed';\n"

    for settings in ("", tls, approved):
        sql = prepare.replace("BEGIN;", "BEGIN;\n" + settings, 1)
        with pytest.raises(DBAPIError, match="NEON_WRITER_NOT_APPROVED"):
            execute_script(r.owner, sql)
        with r.owner.connect() as db:
            assert not db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:name)"), {"name": name})
    try:
        execute_script(r.owner, prepare.replace("BEGIN;", "BEGIN;\n" + tls + approved, 1))
        if r.owner.url.password:
            with r.owner.begin() as db:
                db.exec_driver_sql(f"ALTER ROLE {name} PASSWORD 'mitbol-neon-writer-fixture'")
        writer = create_engine(r.owner.url.set(username=name, password="mitbol-neon-writer-fixture" if r.owner.url.password else None), hide_parameters=True)
        try:
            with pytest.raises(DBAPIError, match="WRITER_NEON_IDENTITY"):
                execute_script(writer, verify)
            verified = verify.replace("SET LOCAL search_path", tls + "SET LOCAL search_path", 1)
            execute_script(writer, verified)
            for statement in ('UPDATE "ProductVariants" SET price=0', 'UPDATE "Products" SET loyverse_id=\'forbidden\'', 'SELECT * FROM public.alembic_version'):
                denied(writer, statement)
            with pytest.raises(DBAPIError, match="NEON_WRITER_EXISTS"):
                execute_script(r.owner, prepare.replace("BEGIN;", "BEGIN;\n" + tls + approved, 1))
        finally:
            writer.dispose()
        # La creación del segundo rol tampoco cambia el perfil original del lector.
        reader_sql = (ROOT / "docs/database/verify_render_reader.sql").read_text(encoding="utf-8")
        reader_sql = "\n".join(line for line in reader_sql.splitlines() if not line.startswith("\\"))
        execute_script(r.reader, reader_sql.replace("mitbol_cloud_dev", r.owner.url.database))
    finally:
        with r.owner.begin() as db:
            if db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:name)"), {"name": name}):
                db.exec_driver_sql(f"DROP OWNED BY {name}")
                db.exec_driver_sql(f"DROP ROLE {name}")


def test_neon_preflight_is_read_only_and_can_be_exercised_on_temporary_copy(writer_database):
    script = (ROOT / "docs/database/preflight_admin_neon.sql").read_text(encoding="utf-8")
    execute_script(writer_database.owner, script.replace("mitbol_cloud_dev", writer_database.owner.url.database))


def initial_writer_password_code():
    import re
    guide = (ROOT / "docs/security/admin_auth_phase5.md").read_text(encoding="utf-8")
    section = guide.split("### 7.4 ", 1)[1].split("### 7.5 ", 1)[0]
    return re.search(r"\$MitbolInitialPasswordCode = @'\n(.*?)\n'@", section, re.S).group(1)


def run_initial_writer_password_code():
    import logging
    previous = logging.root.manager.disable
    try:
        with pytest.raises(SystemExit) as stopped:
            exec(compile(initial_writer_password_code(), "<initial-writer-password>", "exec"), {})
        return stopped.value.code
    finally:
        logging.disable(previous)


@pytest.mark.parametrize("failure", ["no_terminal", "echo_fallback", "query_error"])
def test_initial_writer_password_stops_without_exposing_secrets(monkeypatch, capsys, failure):
    import getpass
    import json
    import warnings
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import psycopg2
    from sqlalchemy.exc import ProgrammingError
    from app.db import connection as policy

    private = "PRIVATE_INITIAL_PASSWORD postgresql://PRIVATE_DSN"
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: failure != "no_terminal"))
    monkeypatch.setattr("sys.stderr", SimpleNamespace(isatty=lambda: True))
    password = MagicMock(return_value=private)
    if failure == "echo_fallback":
        password.side_effect = lambda *args: warnings.warn(private, getpass.GetPassWarning)
    monkeypatch.setattr(getpass, "getpass", password)
    engine, db = MagicMock(), MagicMock()
    engine.begin.return_value.__enter__.return_value = db
    target = SimpleNamespace(expected_host="approved.invalid", expected_database="mitbol_cloud_dev",
                             validate_connection=MagicMock())
    monkeypatch.setattr(policy, "CloudDatabaseTarget", lambda *args: target)
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(policy, "create_database_engine", factory)

    def query(sql, *args):
        result = MagicMock()
        if sql == "SELECT current_user,session_user":
            result.one.return_value = ("neondb_owner",) * 2
        elif sql == "SHOW server_version_num":
            result.scalar_one.return_value = "180006"
        elif sql.startswith("SELECT oid,"):
            result.one.return_value = (123, True, False, False, False, False, False, False, [])
        elif sql.startswith("ALTER ROLE"):
            raise ProgrammingError(private, {}, psycopg2.ProgrammingError(private))
        return result

    db.exec_driver_sql.side_effect = query
    assert run_initial_writer_password_code() == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["status"] == "stopped" and "PRIVATE" not in output.out + output.err
    if failure == "no_terminal":
        password.assert_not_called()
    if failure != "query_error":
        factory.assert_not_called()
    else:
        assert report["stage"] == "initial_password"
        engine.dispose.assert_called_once()


def test_initial_writer_password_preserves_existing_role_and_acl_on_temporary_postgres(writer_database, monkeypatch, capsys):
    import json
    import re
    from types import SimpleNamespace
    from sqlalchemy import create_engine
    from app.db import connection as policy

    r = writer_database
    assert re.fullmatch(r"mitbol_test_auth_[a-f0-9]{32}", r.owner.url.database)
    assert r.owner.url.host == "127.0.0.1" and r.owner.url.username == "mitbol_ci"
    with r.owner.begin() as db:
        db.exec_driver_sql("CREATE ROLE neondb_owner LOGIN CREATEROLE PASSWORD 'mitbol-initial-fixture'")
        db.exec_driver_sql("GRANT mitbol_admin_writer TO neondb_owner WITH ADMIN TRUE, INHERIT FALSE, SET FALSE")
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("sys.stderr", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("getpass.getpass", lambda *args: "mitbol-initial-fixture")
    # Comillas y barra comprueban adaptación de parámetros; sólo datos ficticios.
    bootstrap = "PRIVATE_BOOTSTRAP_'\\_" + "x" * 48
    monkeypatch.setattr("secrets.token_urlsafe", lambda size: bootstrap if size == 48 else pytest.fail("Unexpected entropy"))
    role_sql = "SELECT oid,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls,rolconfig FROM pg_roles WHERE rolname='mitbol_admin_writer'"
    acl_sql = "SELECT oid,relacl::text FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY oid"
    columns_sql = "SELECT attrelid,attnum,attacl::text FROM pg_attribute WHERE attrelid IN (SELECT oid FROM pg_class WHERE relnamespace='public'::regnamespace) ORDER BY attrelid,attnum"
    with r.owner.connect() as db:
        before = (db.exec_driver_sql(role_sql).one(), db.exec_driver_sql(acl_sql).all(), db.exec_driver_sql(columns_sql).all())

    class TemporaryTarget:
        def __init__(self, database, host):
            assert database == "mitbol_cloud_dev" and host == "ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech"
            self.expected_database, self.expected_host = r.owner.url.database, "127.0.0.1"

        def validate_connection(self, db):
            policy.validate_local_connection(db)
            assert db.engine.url.database == r.owner.url.database

    monkeypatch.setattr(policy, "CloudDatabaseTarget", TemporaryTarget)
    def temporary_engine(url, **kwargs):
        assert dict(url.query) == {"sslmode": "verify-full", "channel_binding": "require"}
        assert url.host == "127.0.0.1" and url.database == r.owner.url.database
        # initdb del laboratorio no ofrece TLS. Sólo este adaptador de test lo
        # sustituye y fija su puerto temporal; el código operativo no cambia.
        return create_engine(url.set(query={}, port=r.owner.url.port), hide_parameters=True)
    monkeypatch.setattr(policy, "create_database_engine", temporary_engine)
    try:
        assert run_initial_writer_password_code() == 0
        output = capsys.readouterr()
        assert json.loads(output.out)["status"] == "initial_password_committed"
        assert "PRIVATE" not in output.out + output.err
        with r.owner.connect() as db:
            after = (db.exec_driver_sql(role_sql).one(), db.exec_driver_sql(acl_sql).all(), db.exec_driver_sql(columns_sql).all())
            # Sólo en el laboratorio: el hash verifica el secreto ficticio sin mostrarlo.
            encoded = db.exec_driver_sql("SELECT rolpassword FROM pg_authid WHERE rolname='mitbol_admin_writer'").scalar_one()
            import base64
            import hashlib
            import hmac
            mechanism, parameters, keys = encoded.split("$")
            rounds, salt = parameters.split(":")
            salted = hashlib.pbkdf2_hmac("sha256", bootstrap.encode(), base64.b64decode(salt), int(rounds))
            stored = hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest()
            assert mechanism == "SCRAM-SHA-256"
            assert hmac.compare_digest(base64.b64decode(keys.split(":")[0]), stored)
        assert after == before
        writer = create_engine(r.writer_url.set(password=bootstrap), hide_parameters=True)
        try:
            execute_script(writer, (ROOT / "docs/database/verify_admin_writer_local.sql").read_text(encoding="utf-8"))
        finally:
            writer.dispose()
    finally:
        with r.owner.begin() as db:
            if r.owner.url.password:
                db.exec_driver_sql("ALTER ROLE mitbol_admin_writer PASSWORD 'mitbol-writer-fixture'")
            else:
                db.exec_driver_sql("ALTER ROLE mitbol_admin_writer PASSWORD NULL")
            db.exec_driver_sql("DROP ROLE neondb_owner")

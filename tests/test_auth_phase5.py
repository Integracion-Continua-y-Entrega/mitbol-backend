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


def test_neon_preflight_is_read_only_and_can_be_exercised_on_temporary_copy(writer_database):
    script = (ROOT / "docs/database/preflight_admin_neon.sql").read_text(encoding="utf-8")
    execute_script(writer_database.owner, script.replace("mitbol_cloud_dev", writer_database.owner.url.database))

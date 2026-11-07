"""Perfil Render simulado; sesiones/escrituras reales sólo en PostgreSQL temporal."""

from secrets import token_urlsafe
from types import SimpleNamespace

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
import psycopg2
import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.db.connection import CloudDatabaseTarget, DatabaseTargetError, create_database_engine
from app.db import writer_policy
from app.main import create_app
from app.models import AuditEvent, AuthRateLimit, AuthRateLimitPolicy
from test_admin_writer import backend as writer_backend, writer_database
from test_auth_admin_api import backend as catalog_backend
from test_auth_api import backend as auth_backend, password_hash, PASSWORD
from test_auth_persistence import auth_database
from test_cloud_configuration import cloud_settings, CLOUD_URL


ORIGIN = "https://mitbol-frontend.onrender.com"
BACKEND = "https://mitbol-backend.onrender.com"


def render_config(monkeypatch, **changes):
    monkeypatch.setenv("RENDER", "true")
    values = dict(database_url=CLOUD_URL.replace("reader:", "mitbol_render_reader:"),
        auth_database_url=CLOUD_URL.replace("reader:", "mitbol_admin_writer:"),
        auth_enabled=True, auth_catalog_enabled=True, auth_images_enabled=False,
        auth_cookie_secure=True, auth_cookie_partitioned=True, auth_proxy_mode="render",
        auth_public_origin=BACKEND, auth_origins=[ORIGIN], auth_limit_backend="postgresql",
        auth_limit_secret=SecretStr(token_urlsafe(32)))
    values.update(changes)
    return cloud_settings(**values)


@pytest.mark.parametrize("changes", [
    {"auth_cookie_secure": False}, {"auth_cookie_partitioned": False}, {"auth_proxy_mode": "direct"},
    {"auth_limit_backend": "memory"}, {"auth_limit_secret": None}, {"auth_images_enabled": True},
    {"auth_public_origin": "https://attacker.invalid"}, {"auth_origins": ["*"]},
    {"auth_origins": ["http://localhost:5173"]},
    {"auth_database_url": CLOUD_URL}, {"database_url": CLOUD_URL},
    {"auth_database_url": CLOUD_URL.replace("reader:", "neondb_owner:")},
    {"auth_database_url": CLOUD_URL.replace("verify-full", "require")},
])
def test_render_rejects_incomplete_or_unsafe_profiles(monkeypatch, changes):
    with pytest.raises(ValidationError) as error:
        render_config(monkeypatch, **changes)
    assert "PRIVATE_PASSWORD" not in str(error.value)


def test_render_requires_deployment_marker_and_keeps_disabled_defaults(monkeypatch):
    config = render_config(monkeypatch)
    monkeypatch.delenv("RENDER")
    with pytest.raises(ValueError, match="bloqueado"):
        create_app(config)
    assert not cloud_settings().auth_enabled


def test_cloud_pool_uses_tls_target_and_writer_policy(monkeypatch):
    from app.db import auth_database as pools
    captured = {}
    def engine(value, **kwargs):
        captured.update(kwargs)
        assert "mitbol_admin_writer" in value
        return SimpleNamespace()
    monkeypatch.setattr(pools, "create_database_engine", engine)
    pools.auth_session_factory_for(render_config(monkeypatch))
    assert captured["writer_profile"] is True
    assert captured["target"].expected_database == "mitbol_cloud_dev"
    with pytest.raises(DatabaseTargetError):
        create_database_engine("postgresql://mitbol_admin_writer@localhost/unused", writer_profile=True)


@pytest.fixture
def cloud_backend(writer_backend, monkeypatch):
    b = writer_backend
    from app.db import auth_database as pools
    config = render_config(monkeypatch)
    def writer_engine(value, *, target, writer_profile):
        assert target.expected_host == config.expected_database_host and writer_profile
        return b.roles.writer
    def simulated_tls_target(self, connection):
        # Transporte TLS se prueba separadamente; este HTTP usa PostgreSQL local.
        assert connection.engine.url == b.roles.writer.url
    monkeypatch.setattr(pools, "create_database_engine", writer_engine)
    monkeypatch.setattr(CloudDatabaseTarget, "validate_connection", simulated_tls_target)
    with b.sessions.begin() as db:
        db.execute(delete(AuthRateLimit))
        db.execute(delete(AuthRateLimitPolicy))
    app = create_app(config)
    app.state.session_factory.kw["bind"].dispose()
    app.state.session_factory = sessionmaker(bind=b.roles.reader)
    try:
        with TestClient(app, base_url=BACKEND, headers={"Origin": ORIGIN}) as client:
            yield SimpleNamespace(original=b, app=app, client=client, config=config)
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(AuthRateLimit))
            db.execute(delete(AuthRateLimitPolicy))


def login(b, role="admin", **kwargs):
    return b.client.post("/api/auth/login", headers={"X-CSRF-Protection": "1"},
        json={"email": b.original.emails[role], "password": PASSWORD}, **kwargs)


def test_render_login_partitioned_cookie_logout_and_revocation(cloud_backend):
    b = cloud_backend
    response = login(b)
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert all(part in cookie for part in ("__Host-mitbol_session=", "HttpOnly", "Secure", "SameSite=none", "Partitioned", "Path=/"))
    assert "Domain=" not in cookie
    token = b.client.cookies.get("__Host-mitbol_session")
    assert b.client.get("/api/auth/me").json()["user"]["roles"] == ["admin"]
    assert b.client.get("/api/auth/csrf").json()["csrf_token"] == response.json()["csrf_token"]
    result = b.client.post("/api/auth/logout", json={}, headers={"X-CSRF-Token": response.json()["csrf_token"]})
    assert result.status_code == 204
    assert "Partitioned" in result.headers["set-cookie"] and "Max-Age=0" in result.headers["set-cookie"]
    b.client.cookies.set("__Host-mitbol_session", token)
    assert b.client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_render_role_permissions_and_csrf_keep_audit_atomic(cloud_backend, role):
    b = cloud_backend
    response = login(b, role)
    assert response.status_code == 200
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    path = f"/api/admin/products/{b.original.product_id}"
    assert b.client.patch(path + "/availability", json={"is_available": False}).status_code == 403
    assert b.client.patch(path + "/availability", headers=headers, json={"is_available": False}).status_code == 200
    assert b.client.patch(path + "/description", headers=headers, json={"description": "Render simulado"}).status_code == (200 if role == "admin" else 403)
    with b.original.sessions() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "catalog.product.availability"))
    assert b.client.post(f"/api/admin/variants/{b.original.variant_id}/images", headers=headers, content=b"invalid").status_code == 404


def test_render_rejects_host_origin_duplicates_and_missing_origin(cloud_backend):
    b = cloud_backend
    for headers in ({"Origin": "https://evil.invalid"}, {"Origin": "null"}, {"Host": "evil.invalid"}):
        assert b.client.get("/api/auth/me", headers=headers).status_code == 403
    assert b.client.get("/api/auth/me", headers=[("Origin", ORIGIN), ("Origin", "https://evil.invalid")]).status_code == 403
    b.client.headers.pop("Origin")
    assert b.client.get("/api/auth/me").status_code == 403


def test_render_internal_http_preserves_secure_external_cookie(cloud_backend):
    b = cloud_backend
    async def internal_hop(scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, scheme="http")
        await b.app(scope, receive, send)
    with TestClient(internal_hop, base_url=BACKEND, headers={"Origin": ORIGIN}) as client:
        response = client.post("/api/auth/login", headers={"X-CSRF-Protection": "1"},
            json={"email": b.original.emails["admin"], "password": PASSWORD})
        assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]
        assert client.get("/api/auth/me").status_code == 200
        assert client.options("/api/auth/login", headers={"Origin": "https://evil.invalid",
            "Access-Control-Request-Method": "POST"}).status_code == 400


def test_render_forwarded_ip_cannot_evade_shared_quota(cloud_backend):
    b = cloud_backend
    b.app.state.auth_service.limiter.ip_limit = 2
    for number, expected in ((1, 401), (2, 401), (3, 429)):
        result = b.client.post("/api/auth/login", json={"email": f"missing{number}@example.test", "password": "wrong"},
            headers={"X-CSRF-Protection": "1", "X-Forwarded-For": f"203.0.113.{number}",
                     "X-Forwarded-Host": "attacker.invalid", "X-Forwarded-Proto": "https"})
        assert result.status_code == expected
    assert result.headers["Retry-After"] and b.client.get("/api/menu").status_code == 200


def test_approved_writer_policy_reused_in_read_only_connection(writer_database, monkeypatch):
    r = writer_database
    approved = writer_policy.writer_policy_sql()
    assert "GRANT " not in approved and "CREATE ROLE" not in approved
    monkeypatch.setattr(writer_policy, "writer_policy_sql", lambda: approved.replace("mitbol_cloud_dev", r.owner.url.database))
    for engine, allowed in ((r.writer, True), (r.reader, False), (r.owner, False)):
        raw = engine.raw_connection()
        try:
            if allowed:
                writer_policy.validate_writer_connection(raw.driver_connection)
                with raw.cursor() as cursor:
                    cursor.execute("SHOW transaction_read_only")
                    assert cursor.fetchone()[0] == "on"
            else:
                with pytest.raises(psycopg2.Error, match="WRITER_NEON_IDENTITY"):
                    writer_policy.validate_writer_connection(raw.driver_connection)
        finally:
            raw.rollback()
            raw.close()

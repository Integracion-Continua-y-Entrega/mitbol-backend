"""React localhost → perfil remoto; sin servicios reales ni credenciales externas."""

from io import BytesIO
from datetime import timedelta
from uuid import uuid4

from PIL import Image
from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app.models import AccountSession, AuditEvent
from app.services.account_provisioning import change_access
from test_auth_cloud import (cloud_backend, writer_backend, writer_database, catalog_backend,
                             auth_backend, password_hash, auth_database, render_config, login, CLOUD_URL)


API = "https://mitbol-backend.onrender.com"  # HTTP simulado; nunca se conecta a Render.
ORIGIN = "http://localhost:5173"
REMOTE_ORIGIN = "https://mitbol-frontend.onrender.com"
HOST = "ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech"
DEV = dict(auth_profile="frontend_dev", auth_public_origin=API, auth_origins=[ORIGIN],
           cors_origins=[ORIGIN], expected_database_host=HOST, auth_images_enabled=True,
           database_url=CLOUD_URL.replace("ep-catalog.example.com", HOST).replace("reader:", "mitbol_render_reader:"),
           auth_database_url=CLOUD_URL.replace("ep-catalog.example.com", HOST).replace("reader:", "mitbol_admin_writer:"))
DUAL = DEV | {"auth_origins": [ORIGIN, REMOTE_ORIGIN], "cors_origins": [ORIGIN, REMOTE_ORIGIN]}
REVERSED_DUAL = DUAL | {"auth_origins": [REMOTE_ORIGIN, ORIGIN]}
ORIGIN_CASES = [
    pytest.param(DEV, ORIGIN, id="localhost-original"),
    pytest.param(DUAL, ORIGIN, id="dual-localhost"),
    pytest.param(DUAL, REMOTE_ORIGIN, id="dual-published"),
    pytest.param(REVERSED_DUAL, ORIGIN, id="reversed-localhost"),
    pytest.param(REVERSED_DUAL, REMOTE_ORIGIN, id="reversed-published"),
]


@pytest.mark.parametrize("changes", [
    {"auth_origins": ["*"]}, {"auth_origins": ["null"]},
    {"auth_origins": ["http://127.0.0.1:5173"]},
    {"auth_origins": ["https://mitbol-frontend.onrender.com"]},
    {"auth_origins": []}, {"auth_origins": [ORIGIN, ORIGIN]},
    {"auth_origins": [ORIGIN, REMOTE_ORIGIN, ORIGIN]},
    {"auth_origins": [ORIGIN, REMOTE_ORIGIN, REMOTE_ORIGIN]},
    {"auth_origins": [ORIGIN, "https://evil.invalid"]},
    {"auth_origins": [ORIGIN, REMOTE_ORIGIN, "https://evil.invalid"]},
    {"auth_origins": [ORIGIN + "/"]}, {"auth_origins": ["http://localhost:5174"]},
    {"auth_cookie_secure": False}, {"auth_cookie_partitioned": False},
    {"admin_enabled": True}, {"auth_limit_backend": "memory"},
    {"auth_database_url": DEV["auth_database_url"].replace("verify-full", "require")},
    {"auth_database_url": DEV["auth_database_url"].replace("channel_binding=require", "channel_binding=disable")},
    {"auth_database_url": DEV["auth_database_url"].replace(HOST, "ep-unapproved.neon.tech")},
    {"auth_database_url": DEV["auth_database_url"].replace("mitbol_admin_writer:", "neondb_owner:")},
    {"database_url": DEV["database_url"].replace("mitbol_render_reader:", "mitbol_admin_writer:")},
    {"auth_public_origin": "https://another-service.onrender.com"},
    {"auth_public_origin": "https://another-service.onrender.com", "auth_enabled": False},
    {"auth_profile": "restaurant"},
])
def test_remote_profile_rejects_unsafe_configuration(monkeypatch, changes):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", API)
    with pytest.raises(ValidationError) as error:
        render_config(monkeypatch, **(DEV | changes))
    assert "PRIVATE_PASSWORD" not in str(error.value)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("host", [HOST, HOST.replace(".c-8", "-pooler.c-8")])
@pytest.mark.parametrize("origins", [[ORIGIN], [ORIGIN, REMOTE_ORIGIN], [REMOTE_ORIGIN, ORIGIN]])
def test_remote_profile_accepts_current_academic_target_without_connecting(monkeypatch, host, enabled, origins):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", API)
    changes = dict(expected_database_host=host, auth_enabled=enabled, auth_origins=origins,
                   database_url=DEV["database_url"].replace(HOST, host),
                   auth_database_url=DEV["auth_database_url"].replace(HOST, host))
    config = render_config(monkeypatch, **(DEV | changes))
    assert config.auth_enabled is enabled
    assert config.auth_origins == origins
    assert config.cloud_target.expected_host == host
    assert config.cloud_target.expected_database == "mitbol_cloud_dev"


def test_remote_profile_requires_actual_render_service_and_allows_disabled_preparation(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://another-service.onrender.com")
    with pytest.raises(ValidationError, match="RENDER_EXTERNAL_URL"):
        render_config(monkeypatch, **DEV)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", API)
    assert not render_config(monkeypatch, **(DEV | {"auth_enabled": False})).auth_enabled
    # El perfil normal conserva el bloqueo de localhost y de imágenes cloud.
    for changes in ({"auth_origins": [ORIGIN]}, {"auth_images_enabled": True}):
        with pytest.raises(ValidationError, match="restaurante"):
            render_config(monkeypatch, **changes)


@pytest.mark.parametrize("cloud_backend,origin", ORIGIN_CASES, indirect=["cloud_backend"])
def test_remote_login_cookie_cors_csrf_and_logout(cloud_backend, origin):
    b = cloud_backend
    b.client.headers["Origin"] = origin
    preflight = b.client.options("/api/auth/login", headers={"Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-CSRF-Protection"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert preflight.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in preflight.headers["vary"]
    assert b.client.post("/api/auth/login", json={}).status_code == 403
    response = login(b)
    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert all(part in response.headers["set-cookie"] for part in
               ("HttpOnly", "Secure", "SameSite=none", "Partitioned", "Path=/"))
    assert "Domain=" not in response.headers["set-cookie"]
    token = b.client.cookies.get("__Host-mitbol_session")
    assert b.client.get("/api/auth/me").json()["user"]["roles"] == ["admin"]
    assert b.client.get("/api/auth/csrf").json()["csrf_token"] == response.json()["csrf_token"]
    for rejected in ("null", "http://127.0.0.1:5173", "https://evil.invalid"):
        assert b.client.get("/api/auth/me", headers={"Origin": rejected}).status_code == 403
    if REMOTE_ORIGIN not in b.config.auth_origins:
        assert b.client.get("/api/auth/me", headers={"Origin": REMOTE_ORIGIN}).status_code == 403
    assert b.client.get("/api/auth/me", headers={"Host": "another-service.onrender.com"}).status_code == 403
    assert b.client.get("/api/auth/me", headers=[("Origin", ORIGIN), ("Origin", ORIGIN)]).status_code == 403
    path = f"/api/admin/products/{b.original.product_id}/availability"
    assert b.client.patch(path, json={"is_available": False}).status_code == 403
    assert b.client.patch(path, headers={"X-CSRF-Token": "invalid"}, json={"is_available": False}).status_code == 403
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    assert b.client.post("/api/auth/logout", headers=headers, json={}).status_code == 204
    b.client.cookies.set("__Host-mitbol_session", token)
    assert b.client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("cloud_backend,origin", ORIGIN_CASES[:3], indirect=["cloud_backend"])
@pytest.mark.parametrize("role", ["admin", "staff"])
def test_remote_role_operations_are_audited(cloud_backend, origin, role):
    b = cloud_backend
    b.client.headers["Origin"] = origin
    response = login(b, role)
    assert response.status_code == 200, response.text
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    path = f"/api/admin/products/{b.original.product_id}"
    assert b.client.patch(path + "/availability", headers=headers, json={"is_available": False}).status_code == 200
    assert b.client.patch(path + "/description", headers=headers,
                          json={"description": "Prueba React remota"}).status_code == (200 if role == "admin" else 403)
    tag = b.client.post("/api/admin/tags", headers=headers, json={"name": f"dev-{uuid4().hex}"})
    assert tag.status_code == (201 if role == "admin" else 403)
    if role == "admin":
        link = f"/api/admin/variants/{b.original.variant_id}/tags/{tag.json()['tag_id']}"
        assert b.client.put(link, headers=headers).status_code == 204
        assert b.client.get(link.rsplit("/", 1)[0]).json()["tags"]
        assert b.client.delete(link, headers=headers).status_code == 204
    with b.original.sessions() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "catalog.product.availability"))


@pytest.mark.parametrize("cloud_backend", [DUAL], indirect=True)
@pytest.mark.parametrize("origin", [ORIGIN, REMOTE_ORIGIN])
@pytest.mark.parametrize("expiry", ["idle", "absolute"])
def test_dual_origin_sessions_keep_expiry_limits(cloud_backend, origin, expiry):
    b = cloud_backend
    b.client.headers["Origin"] = origin
    b.app.state.auth_service.clock = lambda: b.original.now.value
    assert login(b).status_code == 200
    account_sessions = select(AccountSession).where(AccountSession.account_id == b.original.ids["admin"][0])
    with b.original.sessions() as db:
        initial_idle = db.scalar(account_sessions).idle_expires_at
    b.original.now.value += timedelta(seconds=b.config.auth_touch_seconds + 1)
    assert b.client.get("/api/auth/me").status_code == 200
    with b.original.sessions.begin() as db:
        session = db.scalar(account_sessions)
        assert initial_idle < session.idle_expires_at <= session.expires_at
        if expiry == "absolute":
            session.idle_expires_at = session.expires_at
        deadline = session.expires_at if expiry == "absolute" else session.idle_expires_at
    b.original.now.value = deadline
    response = b.client.get("/api/auth/me")
    assert response.status_code == 401
    assert "Max-Age=0" in response.headers["set-cookie"]


@pytest.mark.parametrize("cloud_backend", [DUAL], indirect=True)
def test_dual_origin_logout_all_revokes_both_sessions(cloud_backend):
    b, tokens = cloud_backend, {}
    for origin in b.config.auth_origins:
        b.client.cookies.clear()  # Dos sesiones independientes; no simula CHIPS del navegador.
        b.client.headers["Origin"] = origin
        response = login(b)
        assert response.status_code == 200
        tokens[origin] = b.client.cookies.get("__Host-mitbol_session")
    assert b.client.post("/api/auth/logout-all", json={},
        headers={"X-CSRF-Token": response.json()["csrf_token"]}).status_code == 204
    for origin, token in tokens.items():
        b.client.cookies.clear()
        b.client.cookies.set("__Host-mitbol_session", token, domain=b.client.base_url.host, path="/")
        assert b.client.get("/api/auth/me", headers={"Origin": origin}).status_code == 401


@pytest.mark.parametrize("cloud_backend", [DUAL], indirect=True)
def test_dual_origin_cors_preserves_public_orders_and_guest_tracking_scopes(cloud_backend):
    b = cloud_backend
    cases = (
        ("/api/menu", "GET", "", False),
        ("/api/cart/validate", "POST", "Content-Type", False),
        ("/api/cart/summary", "POST", "Content-Type", False),
        ("/api/order-modality/validate", "POST", "Content-Type", False),
        ("/api/orders", "POST", "Content-Type,Idempotency-Key,X-CSRF-Token", True),
        (f"/api/orders/{uuid4()}/tracking", "GET", "Authorization", False),
        (f"/api/orders/{uuid4()}/cancel", "POST", "Content-Type,Authorization,X-CSRF-Token", True),
    )
    for origin in b.config.auth_origins:
        menu = b.client.get("/api/menu", headers={"Origin": origin})
        assert menu.status_code == 200
        assert menu.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in menu.headers
        for path, method, requested_headers, credentials in cases:
            headers = {"Origin": origin, "Access-Control-Request-Method": method}
            if requested_headers:
                headers["Access-Control-Request-Headers"] = requested_headers
            response = b.client.options(path, headers=headers)
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == origin
            assert "Origin" in response.headers["vary"]
            assert (response.headers.get("access-control-allow-credentials") == "true") is credentials
            rejected = b.client.options(path, headers=headers | {"Origin": "https://evil.invalid"})
            assert rejected.status_code == 400 and "access-control-allow-origin" not in rejected.headers
        for path in ("/api/menu", "/api/cart/summary"):
            rejected = b.client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,X-CSRF-Token"})
            assert rejected.status_code == 400


@pytest.mark.parametrize("cloud_backend", [DEV], indirect=True)
def test_remote_dev_images_crud_uses_only_temporary_storage(cloud_backend):
    b = cloud_backend
    response = login(b)
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    content = BytesIO()
    Image.new("RGB", (10, 8), "red").save(content, format="PNG")
    path = f"/api/admin/variants/{b.original.variant_id}/images"
    image = b.client.post(path, headers=headers, files={"file": ("test.png", content.getvalue(), "image/png")})
    assert image.status_code == 201, image.text
    image_path = f"{path}/{image.json()['product_image_id']}"
    assert b.client.get(path).json()["images"]
    assert b.client.patch(image_path, headers=headers, json={"display_order": 2}).status_code == 200
    assert b.client.delete(image_path, headers=headers).status_code == 204
    assert not b.client.get(path).json()["images"]
    staff = login(b, "staff")
    assert b.client.post(path, headers={"X-CSRF-Token": staff.json()["csrf_token"]},
                         files={"file": ("test.png", content.getvalue(), "image/png")}).status_code == 403


@pytest.mark.parametrize("cloud_backend", [DEV], indirect=True)
def test_academic_account_reactivation_keeps_audit_and_revokes_old_session(cloud_backend):
    b = cloud_backend
    assert login(b).status_code == 200
    old_token = b.client.cookies.get("__Host-mitbol_session")
    account_id = b.original.ids["admin"][0]
    with b.original.sessions.begin() as db:
        _, disabled = change_access(db, account_id, account_active=False,
                                    employee_active=False, role_codes=("admin",))
    assert b.client.get("/api/auth/me").status_code == 401
    assert login(b).status_code == 401
    with b.original.sessions.begin() as db:
        _, enabled = change_access(db, account_id, account_active=True,
                                   employee_active=True, role_codes=("admin",))
    assert disabled != enabled
    b.client.cookies.set("__Host-mitbol_session", old_token, domain=b.client.base_url.host, path="/")
    assert b.client.get("/api/auth/me").status_code == 401
    assert login(b).status_code == 200
    assert b.client.get("/api/auth/me").json()["user"]["roles"] == ["admin"]
    with b.original.sessions() as db:
        for reference in (disabled, enabled):
            assert db.scalar(select(AuditEvent).where(AuditEvent.request_id == reference))

"""HU-004: configuración pública del QR, sin conexión a bases reales."""

import json

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.db.database import get_db
from app.main import create_app
from test_cloud_configuration import cloud_settings


@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None),
    ("https://mitbol-frontend.onrender.com", "https://mitbol-frontend.onrender.com/"),
    ("https://menu.example.com/carta", "https://menu.example.com/carta"),
    ("http://localhost:5173/", "http://localhost:5173/"),
    ("http://127.0.0.1:5173/menu", "http://127.0.0.1:5173/menu"),
    ("http://[::1]:5173/", "http://[::1]:5173/"),
])
def test_public_menu_url_accepts_explicit_safe_destinations(value, expected):
    config = Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
                      public_menu_url=value)
    assert config.public_menu_url == expected


@pytest.mark.parametrize("value", [
    "relative/menu", "//menu.example.com/", "javascript:alert(1)", "file:///menu",
    "http://menu.example.com/", "https://menu.example.com/?token=PRIVATE_URL_SECRET",
    "https://menu.example.com/#PRIVATE_URL_SECRET", "https://user:PRIVATE_URL_SECRET@menu.example.com/",
    "https://@menu.example.com/", " https://menu.example.com/", "https://menu.example.com/ ",
    "https://menu.example.com/\n", "https://menu.example.com/a b", "https://menu.example.com\\evil",
])
def test_public_menu_url_rejects_unsafe_or_ambiguous_values_without_echo(value):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
                 public_menu_url=value)
    assert "PRIVATE_URL_SECRET" not in str(error.value)


@pytest.mark.parametrize("value", ["http://localhost:5173/", "https://localhost/", "https://127.0.0.1/"])
def test_cloud_public_menu_url_must_be_remote_https(value):
    with pytest.raises(ValidationError, match="PUBLIC_MENU_URL"):
        cloud_settings(public_menu_url=value)


@pytest.mark.parametrize("value", [None, "https://mitbol-frontend.onrender.com/menu"])
def test_public_config_is_available_with_auth_disabled_and_database_unavailable(monkeypatch, value):
    def forbidden(*args, **kwargs):
        pytest.fail("Consultar la URL del QR intentó abrir PostgreSQL")

    monkeypatch.setattr(Engine, "connect", forbidden)
    application = create_app(cloud_settings(public_menu_url=value, auth_enabled=False, admin_enabled=False))
    application.dependency_overrides[get_db] = forbidden
    with TestClient(application) as client:
        response = client.get("/api/menu/config", headers={"Origin": "https://menu.example.com"})
        assert response.status_code == 200
        assert response.json() == {"menu_url": value}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["access-control-allow-origin"] == "https://menu.example.com"
        assert "access-control-allow-credentials" not in response.headers
        assert "set-cookie" not in response.headers
        assert client.get("/api/auth/me").status_code == 404
        assert client.post("/api/admin/tags", json={"name": "Forbidden"}).status_code == 404
        schema = client.get("/openapi.json").json()
        assert "security" not in schema["paths"]["/api/menu/config"]["get"]
        assert all(set(methods) == {"get"} for methods in schema["paths"].values())
        assert "PRIVATE_PASSWORD" not in json.dumps(schema) + response.text


def test_public_menu_url_is_explicit_instead_of_request_host_or_catalog(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La configuración no debe depender del catálogo")

    monkeypatch.setattr(Engine, "connect", forbidden)
    application = create_app(cloud_settings(public_menu_url="https://menu.example.com/carta"))
    with TestClient(application) as client:
        for host in ("backend.example.com", "attacker.invalid"):
            response = client.get("/api/menu/config", headers={"Host": host, "X-Forwarded-Host": "attacker.invalid"})
            assert response.json() == {"menu_url": "https://menu.example.com/carta"}
        assert client.put("/api/menu/config", json={"menu_url": "https://attacker.invalid/"}).status_code == 405


def test_public_menu_url_loads_from_process_configuration(monkeypatch):
    monkeypatch.setenv("PUBLIC_MENU_URL", "https://menu.example.com/carta")
    assert cloud_settings().public_menu_url == "https://menu.example.com/carta"

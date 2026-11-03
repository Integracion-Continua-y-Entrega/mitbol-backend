"""Configuración cloud sin leer secretos locales ni abrir conexiones."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_settings import SettingsError
import pytest
from sqlalchemy.engine import Engine

from app.core.config import Settings
from app.db.database import get_db
from app.main import create_app


CLOUD_URL = ("postgresql+psycopg2://reader:PRIVATE_PASSWORD@ep-catalog.example.com/mitbol_cloud_dev"
             "?sslmode=verify-full&channel_binding=require")


def cloud_settings(**changes):
    values = dict(_env_file=None, app_env="cloud", database_url=CLOUD_URL,
                  expected_database="mitbol_cloud_dev", expected_database_host="ep-catalog.example.com",
                  cors_origins=["http://localhost:5173", "https://menu.example.com"],
                  admin_enabled=False, loyverse_access_token=None)
    values.update(changes)
    return Settings(**values)


def test_public_cloud_starts_without_loyverse_and_publishes_no_writes(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Iniciar/consultar configuración intentó acceder a PostgreSQL")

    monkeypatch.setattr(Engine, "connect", forbidden)
    config = cloud_settings(image_storage_root=tmp_path / "images")
    assert config.loyverse_access_token is None
    application = create_app(config)
    application.dependency_overrides[get_db] = lambda: SimpleNamespace(
        scalars=lambda statement: SimpleNamespace(all=lambda: []),
    )
    with TestClient(application) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/menu").json() == {"currency": "MXN", "products": []}
        for method in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            assert client.request(method, "/api/admin/tags").status_code == 404
        assert client.post("/api/v1/sync/loyverse").status_code == 404
        schema = client.get("/openapi.json").json()
        assert all(set(methods) == {"get"} for methods in schema["paths"].values())
        assert "securitySchemes" not in schema["components"]
        assert "PRIVATE_PASSWORD" not in json.dumps(schema)
    assert not config.image_storage_root.exists()


@pytest.mark.parametrize("changes", [
    {"admin_enabled": True, "admin_api_key": "PRIVATE_ADMIN_KEY_" * 3},
    {"database_url": "postgresql://reader:PRIVATE_PASSWORD@localhost/mitbol_dev"},
    {"expected_database": None}, {"expected_database_host": None},
    {"database_url": CLOUD_URL.replace("verify-full", "require")},
    {"cors_origins": []}, {"cors_origins": ["http://remote.example.com"]},
    {"app_env": "production"},
])
def test_cloud_configuration_fails_closed_without_secrets(changes):
    with pytest.raises(ValidationError) as error:
        cloud_settings(**changes)
    assert "PRIVATE_PASSWORD" not in str(error.value)
    assert "PRIVATE_ADMIN_KEY_" not in str(error.value)


def test_app_factory_revalidates_copied_cloud_admin_configuration():
    config = cloud_settings().model_copy(update={"admin_enabled": True})
    with pytest.raises(ValueError, match="ADMIN_ENABLED=false"):
        create_app(config)


def test_app_factory_uses_its_own_database_and_closes_sessions(monkeypatch):
    import app.db.database as database

    def forbidden_global_session():
        pytest.fail("La aplicación utilizó la sesión global de otro entorno")

    monkeypatch.setattr(database, "SessionLocal", forbidden_global_session)
    config = cloud_settings()
    application = create_app(config)
    factory = application.state.session_factory
    assert factory.kw["bind"].url.host == config.expected_database_host
    assert factory.kw["bind"].url.database == config.expected_database
    session = MagicMock()
    session.scalars.return_value.all.return_value = []
    sessions = MagicMock(return_value=session)
    sessions.kw = factory.kw
    application.state.session_factory = sessions
    with TestClient(application) as client:
        assert client.get("/api/menu").status_code == 200
    sessions.assert_called_once()
    session.close.assert_called_once()


def test_cloud_never_completes_missing_values_from_dotenv(monkeypatch, tmp_path):
    source = tmp_path / ".env"
    source.write_text("DATABASE_URL=" + CLOUD_URL + "\n"
                      "EXPECTED_DATABASE=mitbol_cloud_dev\n"
                      "EXPECTED_DATABASE_HOST=ep-catalog.example.com\n"
                      'CORS_ORIGINS=["https://menu.example.com"]\n', encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "cloud")
    for name in ("DATABASE_URL", "EXPECTED_DATABASE", "EXPECTED_DATABASE_HOST", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError, match="database_url") as error:
        Settings(_env_file=source)
    assert "PRIVATE_PASSWORD" not in str(error.value)


def test_cloud_requires_explicit_cors_instead_of_local_default(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(_env_file=None, app_env="cloud", database_url=CLOUD_URL,
                 expected_database="mitbol_cloud_dev", expected_database_host="ep-catalog.example.com")


def test_local_dotenv_still_works_but_cannot_activate_cloud(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    source = tmp_path / ".env"
    source.write_text("DATABASE_URL=postgresql://local:fake@localhost/mitbol_dev\nAPP_ENV=local\n", encoding="utf-8")
    assert Settings(_env_file=source).database_url.endswith("/mitbol_dev")
    source.write_text("APP_ENV=cloud\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no en .env"):
        Settings(_env_file=source)


def test_test_mode_does_not_load_dotenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    source = tmp_path / ".env"
    source.write_text("DATABASE_URL=postgresql://private:fake@localhost/mitbol_dev", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings(_env_file=source)


@pytest.mark.parametrize("value", ["local", "test"])
def test_render_requires_explicit_cloud_mode(monkeypatch, value):
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(ValidationError, match="APP_ENV=cloud"):
        Settings(_env_file=None, app_env=value, database_url="postgresql://unused/unused")


@pytest.mark.parametrize("origin", ["*", "null", "https://*.example.com", "https://menu.example.com/",
                                    "https://menu.example.com/path", "https://user:PRIVATE@menu.example.com",
                                    "https://menu.example.com?query=1", "ftp://menu.example.com",
                                    "http://localhost:99999", " https://menu.example.com"])
def test_cors_rejects_non_origins(origin):
    with pytest.raises(ValidationError) as error:
        cloud_settings(cors_origins=[origin])
    assert "PRIVATE" not in str(error.value)


def test_cors_env_is_json_and_http_access_is_limited(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173","https://menu.example.com"]')
    config = Settings(_env_file=None, database_url="postgresql://unused/unused", loyverse_access_token=None)
    application = create_app(config)
    application.dependency_overrides[get_db] = lambda: SimpleNamespace(
        scalars=lambda statement: SimpleNamespace(all=lambda: []),
    )
    with TestClient(application) as client:
        for origin, expected in [("https://menu.example.com", 200), ("https://evil.example.com", 400)]:
            response = client.options("/api/menu", headers={
                "Origin": origin, "Access-Control-Request-Method": "GET",
            })
            assert response.status_code == expected
            assert response.headers.get("access-control-allow-credentials") is None
        assert client.options("/api/menu", headers={
            "Origin": "https://menu.example.com", "Access-Control-Request-Method": "POST",
        }).status_code == 400
    monkeypatch.setenv("CORS_ORIGINS", "not-json")
    with pytest.raises(SettingsError):
        Settings(_env_file=None)

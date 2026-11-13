"""Separación de configuración sin conexiones o lectura de .env."""

from pydantic import SecretStr, ValidationError
import pytest

from app.core.config import Settings
from app.services.sync_worker_runtime import SyncWorkerConfig


TOKEN = "synthetic-worker-token-" + "x" * 32


def web_config(**changes):
    values = dict(_env_file=None, app_env="test", database_url="postgresql://reader@localhost/unused",
        auth_database_url="postgresql://writer@localhost/unused",
        loyverse_store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12", auth_enabled=False, admin_enabled=False)
    values.update(changes)
    return Settings(**values)


@pytest.mark.parametrize("changes", [
    {"sync_worker_url": "http://127.0.0.1:8766"}, {"sync_worker_token": TOKEN},
    {"sync_worker_url": "http://remote.invalid", "sync_worker_token": TOKEN},
    {"sync_worker_url": "http://@localhost", "sync_worker_token": TOKEN},
    {"sync_worker_url": "http://localhost?", "sync_worker_token": TOKEN},
    {"sync_worker_url": "http://localhost#", "sync_worker_token": TOKEN},
    {"sync_worker_url": "http://localhost", "sync_worker_token": "short"},
])
def test_web_worker_settings_fail_closed(changes):
    with pytest.raises(ValidationError):
        web_config(**changes)


def test_web_configuration_requires_no_operational_credentials():
    config = web_config(sync_worker_url="http://127.0.0.1:8766", sync_worker_token=TOKEN)
    assert config.loyverse_access_token is None
    assert config.sync_worker_token.get_secret_value() == TOKEN
    assert config.sync_destination.database == "unused"
    assert ":5432/unused" in config.database_url
    assert ":5432/unused" in config.auth_database_url


@pytest.mark.parametrize("changes", [
    {"auth_database_url": "postgresql://writer@localhost/other"},
    {"auth_database_url": "postgresql://writer@127.0.0.1/unused"},
    {"auth_database_url": "postgresql://writer@localhost:5433/unused"},
    {"auth_database_url": None}, {"loyverse_store_id": None}, {"loyverse_store_id": "invalid"},
])
def test_web_delegation_requires_same_public_and_admin_catalog(changes):
    with pytest.raises(ValidationError):
        web_config(sync_worker_url="http://127.0.0.1:8766", sync_worker_token=TOKEN, **changes)


def test_worker_disabled_does_not_require_shared_catalog():
    config = web_config(auth_database_url="postgresql://writer@localhost/other", loyverse_store_id=None)
    assert config.sync_worker_url is None


@pytest.mark.parametrize("user", ["mitbol_admin_writer", "mitbol_render_reader"])
def test_worker_rejects_web_roles_before_connecting(user):
    with pytest.raises(ValueError):
        SyncWorkerConfig(f"postgresql://{user}@127.0.0.1/mitbol_auth_local", "mitbol_auth_local",
                         "a3a56a06-d40c-4c07-931c-41abcbaaba12", SecretStr("synthetic-provider"), SecretStr(TOKEN))


def test_worker_environment_does_not_complete_credentials_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SYNC_DATABASE_URL=postgresql://operator@127.0.0.1/mitbol_auth_local\n", encoding="utf-8")
    for name in ["SYNC_DATABASE_URL", "SYNC_EXPECTED_DATABASE", "SYNC_EXPECTED_HOST", "SYNC_WORKER_TOKEN", "LOYVERSE_ACCESS_TOKEN", "LOYVERSE_STORE_ID"]:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError):
        SyncWorkerConfig.from_environment()


def test_known_administrative_local_database_is_allowed_but_not_arbitrary_database():
    args = dict(store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12",
                loyverse_token=SecretStr("synthetic-provider"), worker_token=SecretStr(TOKEN))
    SyncWorkerConfig("postgresql://operator@127.0.0.1/mitbol_auth_local", "mitbol_auth_local", **args)
    with pytest.raises(ValueError):
        SyncWorkerConfig("postgresql://operator@127.0.0.1/arbitrary", "arbitrary", **args)

"""Separación de configuración sin conexiones o lectura de .env."""

from pydantic import SecretStr, ValidationError
import pytest

from app.core.config import Settings
from app.services.sync_worker_runtime import SyncWorkerConfig


TOKEN = "synthetic-worker-token-" + "x" * 32


def web_config(**changes):
    return Settings(_env_file=None, app_env="test", database_url="postgresql://reader@localhost/unused",
                    auth_enabled=False, admin_enabled=False, **changes)


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

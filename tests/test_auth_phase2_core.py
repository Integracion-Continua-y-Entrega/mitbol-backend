"""Configuración fail closed y límites locales sin conexiones reales."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from app.core.auth_errors import AuthError
from app.core.auth_limiter import LocalAttemptLimiter, PasswordWorkGate
from app.core.config import Settings
from app.main import create_app


def config(**changes):
    values = dict(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
                  auth_enabled=True, auth_database_url="postgresql://mitbol_ci@localhost/mitbol_auth_local",
                  auth_cookie_secure=False, admin_enabled=False)
    values.update(changes)
    return Settings(**values)


@pytest.mark.parametrize("changes", [
    {"auth_database_url": None},
    {"auth_database_url": "postgresql://postgres@localhost/mitbol_dev"},
    {"auth_database_url": "postgresql://neondb_owner@localhost/mitbol_auth_local"},
    {"auth_database_url": "postgresql://mitbol_render_reader@localhost/mitbol_auth_local"},
    {"auth_database_url": "postgresql://mitbol_ci@remote.example/mitbol_auth_local"},
    {"auth_database_url": "postgresql://mitbol_ci@localhost/mitbol_auth_local?options=-crole=postgres"},
    {"auth_origins": ["https://remote.example"]}, {"auth_origins": ["*"]},
    {"admin_enabled": True, "admin_api_key": "synthetic-local-key-" * 3},
    {"auth_touch_seconds": 1800}, {"auth_idle_seconds": 30000},
])
def test_unsafe_auth_configurations_rejected(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_cloud_blocks_auth_even_if_its_settings_are_modified_after_validation():
    values = dict(app_env="cloud", database_url="postgresql://reader:fake@ep-test.example.com/mitbol_cloud_dev?sslmode=verify-full",
                  expected_database="mitbol_cloud_dev", expected_database_host="ep-test.example.com",
                  cors_origins=["https://menu.example.com"], auth_cookie_secure=True)
    disabled = Settings(_env_file=None, **values)
    for cookie_secure in (True, False):
        with pytest.raises(ValueError):
            create_app(disabled.model_copy(update={"auth_enabled": True, "auth_cookie_secure": cookie_secure}))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**values, "auth_cookie_secure": False})


@pytest.mark.parametrize("variable", ["WEB_CONCURRENCY", "UVICORN_WORKERS"])
def test_more_than_one_worker_is_rejected(monkeypatch, variable):
    monkeypatch.setenv(variable, "2")
    with pytest.raises(ValidationError, match="worker"):
        config()


def test_limiter_counts_atomically_expires_and_does_not_store_emails():
    clock = SimpleNamespace(now=10)
    limiter = LocalAttemptLimiter(account_limit=5, ip_limit=100, clock=lambda: clock.now)
    def attempt(_):
        try:
            limiter.admit("synthetic@example.test", "127.0.0.1")
            return True
        except AuthError as error:
            assert error.status_code == 429 and error.retry_after > 0
            return False
    with ThreadPoolExecutor(max_workers=10) as executor:
        assert sum(executor.map(attempt, range(20))) == 5
    assert "synthetic@example.test" not in repr(limiter._entries)
    clock.now += 900
    assert attempt(None)


def test_ip_limit_cannot_be_bypassed_using_unknown_accounts():
    limiter = LocalAttemptLimiter(account_limit=10, ip_limit=2)
    limiter.admit("first@example.test", "127.0.0.1")
    limiter.admit("second@example.test", "127.0.0.1")
    with pytest.raises(AuthError) as error:
        limiter.admit("third@example.test", "127.0.0.1")
    assert error.value.status_code == 429


def test_limiter_memory_is_bounded_and_fails_closed_instead_of_evicting_live_limits():
    limiter = LocalAttemptLimiter(account_limit=10, ip_limit=10, max_keys=2)
    limiter.admit("first@example.test", "127.0.0.1")
    with pytest.raises(AuthError):
        limiter.admit("second@example.test", "127.0.0.1")
    assert len(limiter._entries) == 2


def test_hash_work_gate_releases_slot_after_failure():
    gate = PasswordWorkGate(1)
    with pytest.raises(RuntimeError), gate.acquire():
        with pytest.raises(AuthError) as error, gate.acquire():
            pass
        assert error.value.status_code == 429
        raise RuntimeError("synthetic")
    with gate.acquire():
        pass

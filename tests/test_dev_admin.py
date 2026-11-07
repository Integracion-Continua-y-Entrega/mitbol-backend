"""El laboratorio sólo puede construirse desde el clúster temporal autorizado."""

import pytest

from app.cli.dev_admin import temporary_target


@pytest.mark.parametrize("mode", ["local", "cloud"])
def test_demo_rejects_non_test_mode_before_connecting(monkeypatch, mode):
    monkeypatch.setenv("APP_ENV", mode)
    with pytest.raises(ValueError):
        temporary_target()


def test_demo_rejects_render_even_in_test_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("RENDER", "true")
    with pytest.raises(ValueError):
        temporary_target()


def test_demo_rejects_arbitrary_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setenv("MITBOL_AUTH_TEST_CLUSTER", str(tmp_path))
    monkeypatch.setenv("MITBOL_AUTH_TEST_PORT", "5432")
    with pytest.raises(ValueError):
        temporary_target()

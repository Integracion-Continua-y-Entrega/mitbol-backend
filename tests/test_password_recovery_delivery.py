"""Enlaces y mensajes privados: captura en memoria, sin correos ni archivos."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.session_tokens import hash_session_token
from app.models import AuditEvent, PasswordResetToken
from app.services.auth_service import AuthService
from test_auth_api import backend as auth_backend, login, NEW_PASSWORD, password_hash
from test_auth_persistence import auth_database
from test_cloud_configuration import cloud_settings
from test_password_recovery import backend, HEADERS, request_recovery, REQUEST, reset


FRONTEND_URL = "https://frontend.example.test/cuenta/restablecer"


def config(**changes):
    return Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused", **changes)


@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), (FRONTEND_URL, FRONTEND_URL),
    ("http://localhost:5173/recuperar", "http://localhost:5173/recuperar"),
    ("http://127.0.0.1:5173/recuperar", "http://127.0.0.1:5173/recuperar"),
    ("http://[::1]:5173/recuperar", "http://[::1]:5173/recuperar"),
])
def test_recovery_url_accepts_trusted_explicit_destinations_without_a_production_default(value, expected):
    assert config(auth_recovery_url=value).auth_recovery_url == expected


@pytest.mark.parametrize("value", [
    "/relative/reset", "//frontend.example.test/reset", "javascript:alert(1)",
    "file:///reset", "http://frontend.example.test/reset", "https://frontend.example.test:70000/reset",
    "https://user:PRIVATE_URL_SECRET@frontend.example.test/reset",
    FRONTEND_URL + "?token=PRIVATE_URL_SECRET", FRONTEND_URL + "#PRIVATE_URL_SECRET",
    FRONTEND_URL + "\n", FRONTEND_URL + " ", " " + FRONTEND_URL, FRONTEND_URL + "\\evil",
])
def test_unsafe_or_ambiguous_configuration_is_rejected_without_echoing_secrets(value):
    with pytest.raises(ValidationError) as error:
        config(auth_recovery_url=value)
    assert "AUTH_RECOVERY_URL" in str(error.value) and "PRIVATE_URL_SECRET" not in str(error.value)


@pytest.mark.parametrize("value", ["http://localhost:5173/recuperar", "https://localhost/recuperar"])
def test_future_cloud_configuration_requires_a_remote_https_recovery_url(value):
    # Solo validación de Settings con destinos sintéticos; no abre conexiones.
    with pytest.raises(ValidationError, match="AUTH_RECOVERY_URL"):
        cloud_settings(auth_recovery_url=value)


def test_recovery_url_loads_from_existing_settings_environment_mechanism(monkeypatch):
    monkeypatch.setenv("AUTH_RECOVERY_URL", FRONTEND_URL)
    assert config().auth_recovery_url == FRONTEND_URL


@pytest.mark.parametrize("url", [None, FRONTEND_URL])
def test_auth_service_uses_only_explicit_recovery_configuration(url):
    settings = config(auth_recovery_url=url, public_menu_url="https://frontend.example.test/menu")
    service = AuthService(None, settings)
    assert service.recovery_url == url and service.recovery_delivery is None


def test_private_message_contains_valid_link_and_instructions_and_can_complete_existing_reset(backend, caplog):
    b = backend
    b.service.recovery_url = config(auth_recovery_url=FRONTEND_URL).auth_recovery_url
    requested = request_recovery(b)
    message, = b.deliveries
    assert requested.status_code == 202 and set(requested.json()) == {"detail"}
    assert message.email == b.emails["customer"]
    assert message.subject == "Restablece tu contraseña de MITBOL"
    assert message.link in message.text and "30 minutos" in message.text and "un solo uso" in message.text
    assert "al menos 15 caracteres" in message.text and "ignora este mensaje" in message.text
    assert message.expires_at == b.now.value + timedelta(minutes=30)
    link = urlsplit(message.link)
    assert link._replace(fragment="").geturl() == FRONTEND_URL and link.query == ""
    token, = parse_qs(link.fragment)["token"]
    assert token == message.token
    assert token not in requested.text + str(dict(requested.headers)) + repr(message) + caplog.text
    assert message.link not in repr(message) and message.email not in repr(message)
    with b.sessions() as db:
        row = db.get(PasswordResetToken, b.ids["customer"][0])
        assert row.token_hash == hash_session_token(token) and row.expires_at == message.expires_at
    assert reset(b, token).status_code == 204
    assert reset(b, token).status_code == 400
    assert login(b, "customer", password=NEW_PASSWORD).status_code == 200


def test_request_parameters_cannot_choose_link_destination(backend):
    b = backend
    b.service.recovery_url = FRONTEND_URL
    response = b.client.post(REQUEST + "?return_url=https://evil.invalid/reset", headers=HEADERS,
                             json={"email": b.emails["customer"]})
    assert response.status_code == 202 and b.deliveries[0].link.startswith(FRONTEND_URL + "#token=")
    assert "evil.invalid" not in b.deliveries[0].text
    for field in ("frontend_url", "return_url", "recovery_url"):
        response = b.client.post(REQUEST, headers=HEADERS,
            json={"email": b.emails["customer"], field: "https://evil.invalid/reset"})
        assert response.status_code == 422
    assert len(b.deliveries) == 1


def test_absent_recovery_url_keeps_generic_response_and_never_delivers_raw_token(backend):
    b = backend
    b.service.recovery_url = None
    response = request_recovery(b)
    unknown = request_recovery(b, "unknown@example.test")
    assert response.status_code == unknown.status_code == 202 and response.json() == unknown.json()
    assert b.deliveries == [] and set(response.json()) == {"detail"}


def test_message_delivery_failure_stays_private_and_does_not_enumerate_accounts(backend, caplog):
    b, captured = backend, []
    b.service.recovery_url = FRONTEND_URL
    def fail(message):
        captured.append(message)
        raise RuntimeError(message.text)
    b.service.recovery_delivery = fail
    response = request_recovery(b)
    assert response.status_code == 202 and response.json() == request_recovery(b, "unknown@example.test").json()
    message, = captured
    assert message.token not in response.text + caplog.text and message.link not in response.text + caplog.text
    with b.sessions() as db:
        assert db.scalar(select(AuditEvent.failure_code).where(
            AuditEvent.action == "auth.password_recovery_delivery")) == "RECOVERY_DELIVERY_FAILED"

"""Brevo simulado: no usa claves reales ni envía correos."""

from datetime import datetime, timedelta, timezone
import json
import logging
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.session_tokens import generate_session_token, hash_session_token
from app.main import create_app
from app.models import AuditEvent, PasswordResetToken
from app.services import password_recovery_email as email_delivery
from app.services.password_recovery_message import build_recovery_message
from test_auth_api import auth_database, backend as auth_backend, login, NEW_PASSWORD, password_hash
from test_auth_phase2_core import config as auth_config
from test_password_recovery import backend, request_recovery, reset


FRONTEND_URL = "https://frontend.example.test/restablecer"
API_KEY = "synthetic-brevo-private-key"
SENDER = "cuentas@example.test"


def config(**changes):
    values = dict(_env_file=None,app_env="test",database_url="postgresql://unused@localhost/unused",
        auth_recovery_url=FRONTEND_URL,auth_recovery_email_provider="brevo",
        auth_brevo_api_key=API_KEY,auth_brevo_from_email=SENDER,auth_brevo_from_name="MITBOL")
    return Settings(**(values | changes))


def notice():
    return build_recovery_message("cliente@example.test",generate_session_token().value,
        datetime.now(timezone.utc)+timedelta(minutes=30),FRONTEND_URL)


def test_brevo_posts_existing_message_with_api_key_and_bounded_timeout(caplog):
    captured = []
    def accepted(request):
        captured.append(request)
        return httpx.Response(201,json={"messageId":"synthetic-message-id"})
    caplog.set_level(logging.DEBUG)
    settings = config()
    delivery = email_delivery.recovery_email_delivery(settings,transport=httpx.MockTransport(accepted))
    message = notice()
    delivery(message)
    request, = captured
    assert request.method == "POST" and str(request.url) == "https://api.brevo.com/v3/smtp/email"
    assert request.headers["api-key"] == API_KEY
    assert request.extensions["timeout"] == dict(connect=10,read=10,write=10,pool=10)
    assert json.loads(request.content) == {
        "sender":{"email":SENDER,"name":"MITBOL"},"to":[{"email":message.email}],
        "subject":message.subject,"textContent":message.text}
    assert message.link in message.text and "30 minutos" in message.text
    for private in (API_KEY,message.token,message.link,message.email):
        assert private not in caplog.text+repr(delivery)+repr(settings)


@pytest.mark.parametrize("status", [400,401,429,500,302,307])
def test_provider_errors_and_redirects_are_private_and_never_retried(status, caplog):
    message = notice()
    calls = []
    def rejected(request):
        calls.append(request)
        return httpx.Response(status,text=API_KEY+message.text,
                              headers={"Location":"https://untrusted.example.test/steal"})
    caplog.set_level(logging.DEBUG)
    delivery = email_delivery.recovery_email_delivery(config(),transport=httpx.MockTransport(rejected))
    with pytest.raises(email_delivery.RecoveryEmailError) as error:
        delivery(message)
    assert len(calls) == 1
    assert str(error.value) == "No fue posible entregar el correo de recuperación."
    for private in (API_KEY,message.token,message.link,message.email):
        assert private not in str(error.value)+caplog.text


@pytest.mark.parametrize("error_type", [httpx.ConnectError,httpx.ReadTimeout])
def test_connection_errors_do_not_expose_private_content(error_type, caplog):
    message = notice()
    def unavailable(request):
        raise error_type(API_KEY+message.text,request=request)
    delivery = email_delivery.recovery_email_delivery(config(),transport=httpx.MockTransport(unavailable))
    with pytest.raises(email_delivery.RecoveryEmailError) as error:
        delivery(message)
    for private in (API_KEY,message.token,message.link):
        assert private not in str(error.value)+caplog.text


@pytest.mark.parametrize("changes", [
    {"auth_recovery_email_provider":"disabled"},{"auth_recovery_url":None},
    {"auth_brevo_api_key":None},{"auth_brevo_api_key":""},{"auth_brevo_api_key":"  "},
    {"auth_brevo_from_email":None},{"auth_brevo_from_name":None},{"auth_brevo_from_name":"  "},
])
def test_absent_configuration_never_connects_or_falls_back_to_configured_smtp(changes, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No debe intentar ninguna conexión")
    monkeypatch.setattr(email_delivery.smtplib,"SMTP",forbidden)
    monkeypatch.setattr(email_delivery.smtplib,"SMTP_SSL",forbidden)
    monkeypatch.setattr(email_delivery.httpx,"Client",forbidden)
    settings = config(auth_smtp_host="smtp.example.test",auth_smtp_username="synthetic-user",
                      auth_smtp_password="synthetic-password",auth_smtp_from=SENDER,**changes)
    assert email_delivery.recovery_email_delivery(settings) is None


def test_provider_selection_is_explicit_and_environment_values_are_private(monkeypatch):
    for name,value in {"AUTH_RECOVERY_EMAIL_PROVIDER":"brevo","AUTH_RECOVERY_URL":FRONTEND_URL,
        "AUTH_BREVO_API_KEY":API_KEY,"AUTH_BREVO_FROM_EMAIL":"CUENTAS@EXAMPLE.TEST",
        "AUTH_BREVO_FROM_NAME":" MITBOL "}.items():
        monkeypatch.setenv(name,value)
    settings = Settings(_env_file=None,app_env="test",database_url="postgresql://unused@localhost/unused")
    assert settings.auth_brevo_api_key.get_secret_value() == API_KEY
    assert settings.auth_brevo_from_email == SENDER and settings.auth_brevo_from_name == "MITBOL"
    assert API_KEY not in repr(settings)
    assert isinstance(email_delivery.recovery_email_delivery(settings),email_delivery.BrevoRecoveryDelivery)
    with pytest.raises(ValidationError):
        config(auth_recovery_email_provider="auto")


@pytest.mark.parametrize("changes", [
    {"auth_brevo_from_email":"not-an-email"},
    {"auth_brevo_from_email":"cuentas@example.test\r\nBcc:synthetic-private@example.test"},
    {"auth_brevo_from_name":"MITBOL\nsynthetic-private"},
])
def test_invalid_sender_configuration_does_not_echo_input(changes):
    with pytest.raises(ValidationError) as error:
        config(**changes)
    assert "synthetic-private" not in str(error.value)


@pytest.mark.parametrize("configured", [False,True])
def test_app_factory_wires_brevo_without_network_and_other_endpoints_keep_working(configured, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("El inicio de la aplicación no debe enviar correo")
    values = {key:value for key,value in config().model_dump().items()
        if key.startswith("auth_brevo_") or key in {"auth_recovery_email_provider","auth_recovery_url"}}
    if not configured:
        values["auth_brevo_api_key"] = None
    settings = auth_config(**values)
    monkeypatch.setattr(email_delivery.httpx,"Client",forbidden)
    app = create_app(settings)
    if configured:
        assert isinstance(app.state.auth_service.recovery_delivery,email_delivery.BrevoRecoveryDelivery)
    else:
        assert app.state.auth_service.recovery_delivery is None
    with TestClient(app) as client:
        assert client.get("/").status_code == 200


def test_brevo_sends_after_commit_and_completes_existing_single_use_reset(backend, caplog):
    b = backend
    b.service.recovery_url = FRONTEND_URL
    captured = []
    def delivered(request):
        text = json.loads(request.content)["textContent"]
        link = next(line for line in text.splitlines() if line.startswith(FRONTEND_URL))
        token, = parse_qs(urlsplit(link).fragment)["token"]
        with b.sessions() as db:
            assert db.get(PasswordResetToken,b.ids["customer"][0]).token_hash == hash_session_token(token)
        captured.append(token)
        return httpx.Response(201,json={"messageId":"synthetic-message-id"})
    b.service.recovery_delivery = email_delivery.recovery_email_delivery(config(),
        transport=httpx.MockTransport(delivered))
    response = request_recovery(b)
    assert response.status_code == 202 and response.json() == request_recovery(b,"unknown@example.test").json()
    token, = captured
    assert token not in response.text+caplog.text and API_KEY not in response.text+caplog.text
    assert reset(b,token).status_code == 204 and reset(b,token).status_code == 400
    assert login(b,"customer",password=NEW_PASSWORD).status_code == 200


def test_brevo_failure_keeps_generic_response_safe_audit_and_existing_login(backend, caplog):
    b = backend
    b.service.recovery_url = FRONTEND_URL
    captured = []
    def rejected(request):
        text = json.loads(request.content)["textContent"]
        captured.append(text)
        return httpx.Response(500,text=API_KEY+text)
    b.service.recovery_delivery = email_delivery.recovery_email_delivery(config(),
        transport=httpx.MockTransport(rejected))
    response = request_recovery(b)
    assert response.status_code == 202 and response.json() == request_recovery(b,"unknown@example.test").json()
    text, = captured
    link = next(line for line in text.splitlines() if line.startswith(FRONTEND_URL))
    token, = parse_qs(urlsplit(link).fragment)["token"]
    for private in (API_KEY,b.emails["customer"],token,link):
        assert private not in response.text+caplog.text
    with b.sessions() as db:
        assert db.scalar(select(AuditEvent.failure_code).where(
            AuditEvent.action == "auth.password_recovery_delivery")) == "RECOVERY_DELIVERY_FAILED"
    assert login(b,"customer").status_code == 200

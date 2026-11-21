"""SMTP simulado: nunca abre conexiones ni envía correos reales."""

from datetime import datetime, timedelta, timezone
import smtplib
import ssl
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
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
USERNAME = "synthetic-smtp-user"
PASSWORD = "synthetic-smtp-password"
SENDER = "cuentas@example.test"


def config(**changes):
    values = dict(_env_file=None,app_env="test",database_url="postgresql://unused@localhost/unused",
        auth_recovery_url=FRONTEND_URL,auth_smtp_host="smtp.example.test",auth_smtp_port=587,
        auth_smtp_security="starttls",auth_smtp_username=USERNAME,auth_smtp_password=PASSWORD,
        auth_smtp_from=SENDER,auth_smtp_timeout_seconds=4.5)
    return Settings(**(values | changes))


@pytest.fixture
def smtp(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.send_message.return_value = {}
    for name in ("SMTP","SMTP_SSL"):
        monkeypatch.setattr(email_delivery.smtplib,name,MagicMock(return_value=client))
    return client


def notice():
    return build_recovery_message("cliente@example.test",generate_session_token().value,
        datetime.now(timezone.utc)+timedelta(minutes=30),FRONTEND_URL)


@pytest.mark.parametrize("mode,port", [("starttls",587),("ssl",465)])
def test_smtp_encrypts_before_authentication_and_sends_existing_private_message(smtp, mode, port, caplog):
    settings = config(auth_smtp_security=mode,auth_smtp_port=port)
    delivery = email_delivery.smtp_recovery_delivery(settings)
    message = notice()
    delivery(message)
    constructor = email_delivery.smtplib.SMTP if mode == "starttls" else email_delivery.smtplib.SMTP_SSL
    other = email_delivery.smtplib.SMTP_SSL if mode == "starttls" else email_delivery.smtplib.SMTP
    assert constructor.call_count == 1
    assert {key:constructor.call_args.kwargs[key] for key in ("host","port","timeout")} == {
        "host":"smtp.example.test","port":port,"timeout":4.5}
    other.assert_not_called()
    context = (smtp.starttls.call_args.kwargs["context"] if mode == "starttls"
               else constructor.call_args.kwargs["context"])
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    steps = [call[0] for call in smtp.mock_calls if call[0] in {"ehlo","starttls","login","send_message"}]
    assert steps == (["ehlo","starttls","ehlo","login","send_message"] if mode == "starttls"
                     else ["ehlo","login","send_message"])
    smtp.login.assert_called_once_with(USERNAME,PASSWORD)
    sent = smtp.send_message.call_args.args[0]
    assert str(sent["From"]) == SENDER and str(sent["To"]) == message.email
    assert sent["Date"] and sent["Message-ID"]
    assert str(sent["Subject"]) == message.subject and sent.get_content().strip() == message.text.strip()
    assert message.link in sent.get_content() and "30 minutos" in sent.get_content()
    assert smtp.send_message.call_args.kwargs == {"from_addr":SENDER,"to_addrs":[message.email]}
    assert message.token not in repr(delivery)+repr(settings)+caplog.text
    assert PASSWORD not in repr(settings)+repr(delivery)+caplog.text


@pytest.mark.parametrize("missing", ["auth_recovery_url","auth_smtp_host","auth_smtp_username",
                                    "auth_smtp_password","auth_smtp_from"])
def test_partial_or_absent_delivery_configuration_never_connects(smtp, missing):
    assert email_delivery.smtp_recovery_delivery(config(**{missing:None})) is None
    email_delivery.smtplib.SMTP.assert_not_called()
    email_delivery.smtplib.SMTP_SSL.assert_not_called()


def test_smtp_settings_load_from_environment_without_secret_values_in_repr(monkeypatch):
    for name,value in {"AUTH_RECOVERY_URL":FRONTEND_URL,"AUTH_SMTP_HOST":"smtp.example.test",
        "AUTH_SMTP_PORT":"465","AUTH_SMTP_SECURITY":"ssl","AUTH_SMTP_USERNAME":USERNAME,
        "AUTH_SMTP_PASSWORD":PASSWORD,"AUTH_SMTP_FROM":SENDER,"AUTH_SMTP_TIMEOUT_SECONDS":"7"}.items():
        monkeypatch.setenv(name,value)
    settings = Settings(_env_file=None,app_env="test",database_url="postgresql://unused@localhost/unused")
    assert settings.auth_smtp_port == 465 and settings.auth_smtp_security == "ssl"
    assert settings.auth_smtp_username.get_secret_value() == USERNAME
    assert settings.auth_smtp_password.get_secret_value() == PASSWORD and settings.auth_smtp_timeout_seconds == 7
    assert email_delivery.smtp_recovery_delivery(settings) is not None
    assert USERNAME not in repr(settings) and PASSWORD not in repr(settings)


@pytest.mark.parametrize("changes", [
    {"auth_smtp_security":"plain"},{"auth_smtp_port":0},{"auth_smtp_port":65536},
    {"auth_smtp_timeout_seconds":0},{"auth_smtp_timeout_seconds":61},{"auth_smtp_timeout_seconds":"nan"},
    {"auth_smtp_host":"smtp://user:synthetic-private-secret@smtp.example.test"},
    {"auth_smtp_host":"smtp.example.test\n"},{"auth_smtp_from":"missing-address"},
    {"auth_smtp_from":"cuentas@example.test\r\nBcc:synthetic-private-secret@example.test"},
])
def test_unsafe_transport_configuration_is_rejected_without_echoing_private_input(changes):
    with pytest.raises(ValidationError) as error:
        config(**changes)
    assert "synthetic-private-secret" not in str(error.value)


@pytest.mark.parametrize("configured", [False,True])
def test_app_factory_wires_callback_lazily_and_starts_without_smtp(configured, smtp):
    settings = auth_config(**({key:value for key,value in config().model_dump().items()
                             if key.startswith("auth_smtp_") or key == "auth_recovery_url"} if configured else {}))
    app = create_app(settings)
    if configured:
        assert isinstance(app.state.auth_service.recovery_delivery,email_delivery.SmtpRecoveryDelivery)
    else:
        assert app.state.auth_service.recovery_delivery is None
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
    email_delivery.smtplib.SMTP.assert_not_called()
    email_delivery.smtplib.SMTP_SSL.assert_not_called()


def test_unvalidated_security_mode_never_falls_back_to_plaintext(smtp):
    settings = config().model_copy(update={"auth_smtp_security":"plain"})
    with pytest.raises(email_delivery.RecoveryEmailError):
        email_delivery.smtp_recovery_delivery(settings)(notice())
    email_delivery.smtplib.SMTP.assert_not_called()
    email_delivery.smtplib.SMTP_SSL.assert_not_called()
    smtp.login.assert_not_called()


@pytest.mark.parametrize("stage", ["connect","tls","login","send","refused","quit"])
def test_provider_errors_are_private_and_tls_failure_never_authenticates(smtp, stage, caplog):
    message = notice()
    private = message.token+PASSWORD+message.email
    if stage == "connect":
        email_delivery.smtplib.SMTP.side_effect = TimeoutError(private)
    elif stage == "tls":
        smtp.starttls.side_effect = smtplib.SMTPNotSupportedError(private)
    elif stage == "login":
        smtp.login.side_effect = smtplib.SMTPAuthenticationError(535,private.encode())
    elif stage == "send":
        smtp.send_message.side_effect = smtplib.SMTPDataError(554,private.encode())
    elif stage == "refused":
        smtp.send_message.return_value = {message.email:(550,private)}
    else:
        smtp.__exit__.side_effect = OSError(private)
    with pytest.raises(email_delivery.RecoveryEmailError) as error:
        email_delivery.smtp_recovery_delivery(config())(message)
    assert str(error.value) == "No fue posible entregar el correo de recuperación."
    for value in (message.token,PASSWORD,message.email):
        assert value not in str(error.value)+caplog.text
    if stage in {"connect","tls"}:
        smtp.login.assert_not_called()
        smtp.send_message.assert_not_called()


def test_smtp_message_is_sent_after_commit_and_completes_existing_reset(backend, smtp, caplog):
    b = backend
    b.service.recovery_url = FRONTEND_URL
    b.service.recovery_delivery = email_delivery.smtp_recovery_delivery(config())
    captured = []
    def delivered(message, **envelope):
        link = next(line for line in message.get_content().splitlines() if line.startswith(FRONTEND_URL))
        token, = parse_qs(urlsplit(link).fragment)["token"]
        with b.sessions() as db:
            assert db.get(PasswordResetToken,b.ids["customer"][0]).token_hash == hash_session_token(token)
        captured.append(token)
        return {}
    smtp.send_message.side_effect = delivered
    response = request_recovery(b)
    assert response.status_code == 202 and response.json() == request_recovery(b,"unknown@example.test").json()
    smtp.send_message.assert_called_once()
    token, = captured
    assert token not in response.text+caplog.text and PASSWORD not in response.text+caplog.text
    assert reset(b,token).status_code == 204 and reset(b,token).status_code == 400
    assert login(b,"customer",password=NEW_PASSWORD).status_code == 200


def test_smtp_failure_keeps_generic_http_response_and_does_not_break_login(backend, smtp, caplog):
    b = backend
    b.service.recovery_url = FRONTEND_URL
    b.service.recovery_delivery = email_delivery.smtp_recovery_delivery(config())
    smtp.send_message.side_effect = smtplib.SMTPDataError(554,(PASSWORD+b.emails["customer"]).encode())
    response = request_recovery(b)
    assert response.status_code == 202 and response.json() == request_recovery(b,"unknown@example.test").json()
    sent = smtp.send_message.call_args.args[0].get_content()
    link = next(line for line in sent.splitlines() if line.startswith(FRONTEND_URL))
    token, = parse_qs(urlsplit(link).fragment)["token"]
    assert all(value not in response.text+caplog.text for value in (PASSWORD,b.emails["customer"],token))
    with b.sessions() as db:
        assert db.scalar(select(AuditEvent.failure_code).where(
            AuditEvent.action == "auth.password_recovery_delivery")) == "RECOVERY_DELIVERY_FAILED"
    assert login(b,"customer").status_code == 200

"""Transportes privados; sin conexión hasta invocar el callback de HU-013."""

from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import smtplib
import ssl

import httpx


class RecoveryEmailError(RuntimeError):
    """Error seguro: nunca incluye respuestas del proveedor ni el mensaje."""


class SmtpRecoveryDelivery:
    def __init__(self, config):
        self._config = config

    def __call__(self, notice):
        config = self._config
        try:
            message = EmailMessage()
            sender = Address(addr_spec=config.auth_smtp_from)
            message["From"] = sender
            message["To"] = Address(addr_spec=notice.email)
            message["Subject"] = notice.subject
            message["Date"] = formatdate(usegmt=True)
            message["Message-ID"] = make_msgid(domain=sender.domain)
            message.set_content(notice.text)
            context = ssl.create_default_context()
            options = {"host":config.auth_smtp_host, "port":config.auth_smtp_port,
                       "timeout":config.auth_smtp_timeout_seconds}
            if config.auth_smtp_security == "ssl":
                connection = smtplib.SMTP_SSL(**options, context=context)
            elif config.auth_smtp_security == "starttls":
                connection = smtplib.SMTP(**options)
            else:
                raise RecoveryEmailError
            with connection as smtp:
                smtp.ehlo()
                if config.auth_smtp_security == "starttls":
                    # Sin fallback a texto plano cuando STARTTLS no está disponible.
                    smtp.starttls(context=context)
                    smtp.ehlo()
                smtp.login(config.auth_smtp_username.get_secret_value(), config.auth_smtp_password.get_secret_value())
                refused = smtp.send_message(message, from_addr=config.auth_smtp_from, to_addrs=[notice.email])
                if refused:
                    raise RecoveryEmailError
        except Exception:
            raise RecoveryEmailError("No fue posible entregar el correo de recuperación.") from None


def smtp_recovery_delivery(config):
    """Configuración ausente/parcial: no conecta ni afirma que se envíe correo."""
    if not (config.auth_recovery_url and config.auth_smtp_host and config.auth_smtp_from
            and config.auth_smtp_username and config.auth_smtp_username.get_secret_value()
            and config.auth_smtp_password and config.auth_smtp_password.get_secret_value()):
        return None
    return SmtpRecoveryDelivery(config)


class BrevoRecoveryDelivery:
    def __init__(self, config, *, transport=None):
        self._config = config
        self._transport = transport

    def __call__(self, notice):
        config = self._config
        try:
            # Destino fijo y sin redirecciones: la clave nunca viaja a otra URL.
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False,
                              transport=self._transport) as client:
                response = client.post("https://api.brevo.com/v3/smtp/email",
                    headers={"api-key":config.auth_brevo_api_key.get_secret_value()},
                    json={"sender":{"email":config.auth_brevo_from_email,
                                    "name":config.auth_brevo_from_name},
                          "to":[{"email":notice.email}], "subject":notice.subject,
                          "textContent":notice.text})
                if response.status_code != 201:
                    raise RecoveryEmailError
        except Exception:
            # No propagar el cuerpo del proveedor ni excepciones con datos privados.
            raise RecoveryEmailError("No fue posible entregar el correo de recuperación.") from None


def recovery_email_delivery(config, *, transport=None):
    """Proveedor explícito, sin fallback ni envíos con configuración incompleta."""
    if config.auth_recovery_email_provider == "smtp":
        return smtp_recovery_delivery(config)
    if config.auth_recovery_email_provider == "brevo":
        if (config.auth_recovery_url and config.auth_brevo_from_email and config.auth_brevo_from_name
                and config.auth_brevo_api_key and config.auth_brevo_api_key.get_secret_value().strip()):
            return BrevoRecoveryDelivery(config, transport=transport)
    return None

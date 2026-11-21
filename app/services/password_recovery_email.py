"""Entrega SMTP privada; sin conexión hasta invocar el callback de HU-013."""

from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import smtplib
import ssl


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

"""Mensaje privado en memoria; no realiza envíos ni escribe enlaces en archivos."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode

from app.core.passwords import MIN_PASSWORD_LENGTH
from app.core.session_tokens import hash_session_token


@dataclass(frozen=True)
class PasswordRecoveryNotice:
    email: str = field(repr=False)
    token: str = field(repr=False)
    expires_at: datetime
    link: str = field(repr=False)
    subject: str
    text: str = field(repr=False)


def build_recovery_message(email, token, expires_at, frontend_url):
    """frontend_url procede exclusivamente de Settings, nunca de la solicitud."""
    hash_session_token(token)
    # El fragmento no se transmite al servidor HTTP del frontend ni en Referer.
    link = frontend_url + "#" + urlencode({"token": token})
    expiry = expires_at.astimezone(timezone.utc).strftime("%d/%m/%Y a las %H:%M UTC")
    text = (
        "Recibimos una solicitud para restablecer la contraseña de tu cuenta MITBOL.\n\n"
        f"Abre este enlace y elige una nueva contraseña de al menos {MIN_PASSWORD_LENGTH} caracteres:\n{link}\n\n"
        f"El enlace es de un solo uso y vence el {expiry}, 30 minutos después de la solicitud. "
        "Si solicitas otro enlace, el anterior dejará de ser válido.\n\n"
        "Si no solicitaste este cambio, ignora este mensaje. Tu contraseña no ha cambiado.\n"
        "No compartas este enlace con otras personas."
    )
    return PasswordRecoveryNotice(email, token, expires_at, link, "Restablece tu contraseña de MITBOL", text)

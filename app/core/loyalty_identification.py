"""Credencial QR opaca con espacio propio; no es una sesión ni un enlace público."""

from dataclasses import dataclass, field
from hashlib import sha256

from app.core.session_tokens import generate_session_token, hash_session_token


QR_PREFIX = "mitbol:loyalty:v1:"
QR_VALUE_LENGTH = len(QR_PREFIX) + 43


@dataclass(frozen=True)
class IdentificationToken:
    qr_payload: str = field(repr=False)
    digest: str = field(repr=False)


def hash_identification(qr_payload: str) -> str:
    """Valida el formato canónico antes de calcular un hash distinto al de las cookies de sesión."""
    if not isinstance(qr_payload, str) or not qr_payload.startswith(QR_PREFIX):
        raise ValueError("Identificación de fidelización inválida.")
    try:
        hash_session_token(qr_payload[len(QR_PREFIX):])
    except ValueError:
        raise ValueError("Identificación de fidelización inválida.") from None
    return sha256(qr_payload.encode("ascii")).hexdigest()


def generate_identification() -> IdentificationToken:
    """Genera 256 bits aleatorios sin incluir IDs, nombres ni material de la sesión."""
    payload = QR_PREFIX + generate_session_token().value
    return IdentificationToken(payload, hash_identification(payload))

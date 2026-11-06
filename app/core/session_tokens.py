"""Material opaco: sólo token_hash se persiste; ningún token en repr o logs."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, field
from hashlib import sha256
import re
from secrets import compare_digest, token_urlsafe


@dataclass(frozen=True)
class SessionToken:
    value: str = field(repr=False)
    digest: str = field(repr=False)


def hash_session_token(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("Identificador de sesión inválido.")
    decoded = urlsafe_b64decode(value + "=")
    if urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise ValueError("Identificador de sesión inválido.")
    return sha256(value.encode("ascii")).hexdigest()


def generate_session_token() -> SessionToken:
    value = token_urlsafe(32)
    return SessionToken(value, hash_session_token(value))


def session_token_matches(value: str, digest: str) -> bool:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    try:
        expected = hash_session_token(value)
    except ValueError:
        return False
    return compare_digest(expected, digest)

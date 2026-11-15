"""Primitiva OAuth2 preparada, sin router público, registro ni consumo de eventos.

Contrato oficial: HMAC-SHA1 hexadecimal minúsculo del cuerpo original, usando
Client Secret UTF-8. Un PAT no sustituye ese secreto ni autentica notificaciones.
"""

import hashlib
import hmac
import re


def verify_signature(raw_body: bytes, signature: str | None, client_secret: str | None) -> bool:
    if (not isinstance(client_secret, str) or not client_secret
            or not isinstance(raw_body, bytes) or len(raw_body) > 1_048_576
            or not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{40}", signature)):
        return False
    expected = hmac.new(client_secret.encode("utf-8"), raw_body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, signature)

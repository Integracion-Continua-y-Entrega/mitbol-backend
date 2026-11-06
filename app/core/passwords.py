"""Argon2id únicamente; no importa Settings ni establece conexiones."""

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


# 64 MiB, tres pasadas y un hilo. Medir concurrencia antes de publicar login.
_hasher = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=1,
                         hash_len=32, salt_len=16, type=Type.ID)
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 1024


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError("La contraseña debe tener entre 15 y 1024 caracteres.")
    # No recortar, normalizar ni truncar contraseñas.
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if (not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH
            or not isinstance(password_hash, str) or not password_hash.startswith("$argon2id$")):
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    if not isinstance(password_hash, str) or not password_hash.startswith("$argon2id$"):
        return True
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True

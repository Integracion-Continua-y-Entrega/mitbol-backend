"""Normalización compartida por las futuras altas y el login; sin E/S."""


def normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("El correo debe ser texto.")
    value = value.strip().lower()
    if (not 3 <= len(value) <= 254 or value.count("@") != 1
            or any(character.isspace() or ord(character) < 32 for character in value)
            or not all(value.split("@"))):
        raise ValueError("El correo no tiene un formato admitido.")
    return value

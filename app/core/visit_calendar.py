"""Configuración compartida del calendario; la existencia de la zona se comprueba en PostgreSQL."""

import re


DEFAULT_RESTAURANT_TIMEZONE = "America/Mexico_City"


def valid_timezone_name(value) -> bool:
    return bool(isinstance(value, str) and 1 <= len(value) <= 64
                and re.fullmatch(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*", value))

"""Política fija de MITBOL; los códigos no son roles técnicos PostgreSQL."""

from collections.abc import Iterable
from types import MappingProxyType


ROLE_PERMISSIONS = MappingProxyType({
    "admin": frozenset({"catalog:read", "description:write", "availability:write",
                        "images:write", "tags:write", "settings:write"}),
    "staff": frozenset({"catalog:read", "availability:write"}),
})


def permissions_for_roles(role_codes: Iterable[str], *, employee_active: bool) -> frozenset[str]:
    if not employee_active:
        return frozenset()
    return frozenset(permission for code in role_codes for permission in ROLE_PERMISSIONS.get(code, ()))

"""Registro del catálogo (Base) y de identidad compartida (AuthBase)."""

from app.db.base import Base
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.product_modifier import ProductModifier
from app.models.product_complement import ProductComplement
from app.models.product_modifier_assignment import ProductModifierAssignment
from app.models.product_image import ProductImage
from app.models.tag import Tag
from app.models.product_tag import ProductTag
from app.db.auth_base import AuthBase
from app.models.auth import AccountSession, AuditEvent, Employee, EmployeeRole, Person, PersonalAccount, Role
from app.models.auth_limits import AuthRateLimit, AuthRateLimitPolicy

__all__ = [
    "Base", "Product", "ProductVariant", "ProductModifier",
    "ProductComplement", "ProductModifierAssignment",
    "ProductImage", "Tag", "ProductTag",
    "AuthBase", "Person", "PersonalAccount", "Employee", "Role", "EmployeeRole",
    "AccountSession", "AuditEvent",
    "AuthRateLimit", "AuthRateLimitPolicy",
]

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
from sqlalchemy import MetaData

# Perfil explícito de HU-008/HU-007 (0003): los comandos y pruebas históricos
# no deben incorporar tablas futuras al recorrer el registro ORM actual.
catalog_metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))
for _table in tuple(Base.metadata.tables.values()):
    _table.to_metadata(catalog_metadata)

from app.models.discount import Discount
from app.db.auth_base import AuthBase
from app.models.auth import AccountSession, AuditEvent, Employee, EmployeeRole, Person, PersonalAccount, Role
from app.models.auth_limits import AuthRateLimit, AuthRateLimitPolicy

__all__ = [
    "Base", "catalog_metadata", "Discount", "Product", "ProductVariant", "ProductModifier",
    "ProductComplement", "ProductModifierAssignment",
    "ProductImage", "Tag", "ProductTag",
    "AuthBase", "Person", "PersonalAccount", "Employee", "Role", "EmployeeRole",
    "AccountSession", "AuditEvent",
    "AuthRateLimit", "AuthRateLimitPolicy",
]

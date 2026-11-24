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
from app.models.commercial_category import CommercialCategory
from sqlalchemy import MetaData

# Perfil explícito de HU-008/HU-007 (0003): los comandos y pruebas históricos
# no deben incorporar tablas futuras al recorrer el registro ORM actual.
catalog_metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))
for _table in tuple(Base.metadata.tables.values()):
    if _table.name == "Categories":
        continue
    _table.to_metadata(catalog_metadata)

# La columna/FK 0008 no pertenece al perfil histórico 0003.
_historical_product = catalog_metadata.tables["Products"]
_historical_product.foreign_keys.difference_update(_historical_product.c.category_id.foreign_keys)
for _constraint in tuple(_historical_product.constraints):
    if any(column.name == "category_id" for column in _constraint.columns):
        _historical_product.constraints.remove(_constraint)
for _index in tuple(_historical_product.indexes):
    if any(column.name == "category_id" for column in _index.columns):
        _historical_product.indexes.remove(_index)
_historical_product._columns.remove(_historical_product.c.category_id)

runtime_catalog_metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))
for _table in tuple(Base.metadata.tables.values()):
    _table.to_metadata(runtime_catalog_metadata)

from app.models.discount import Discount
pre_categories_metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))
for _table in (*catalog_metadata.tables.values(), Discount.__table__):
    _table.to_metadata(pre_categories_metadata)
from app.db.auth_base import AuthBase
from app.models.auth import AccountSession, AuditEvent, Employee, EmployeeRole, Person, PersonalAccount, Role
from app.models.auth_limits import AuthRateLimit, AuthRateLimitPolicy
from app.models.password_recovery import PasswordRecoveryBase, PasswordResetToken
from app.models.coupon import CouponBase, CustomerCoupon
from app.models.order import OrderBase, Order, OrderDetail, OrderDetailComplement, RestaurantOrderSettings
from app.models.commercial_discount import CommercialDiscountBase, CommercialDiscountAssignment, OrderDiscountSnapshot
from app.models.order_status_history import OrderStateBase, OrderStatusHistory
from app.models.order_payment import OrderPaymentBase, OrderPayment

__all__ = [
    "Base", "catalog_metadata", "runtime_catalog_metadata", "pre_categories_metadata",
    "CommercialCategory", "Discount", "Product", "ProductVariant", "ProductModifier",
    "ProductComplement", "ProductModifierAssignment",
    "ProductImage", "Tag", "ProductTag",
    "AuthBase", "Person", "PersonalAccount", "Employee", "Role", "EmployeeRole",
    "AccountSession", "AuditEvent",
    "AuthRateLimit", "AuthRateLimitPolicy",
    "PasswordRecoveryBase", "PasswordResetToken",
    "CouponBase", "CustomerCoupon",
    "OrderBase", "Order", "OrderDetail", "OrderDetailComplement", "RestaurantOrderSettings",
    "CommercialDiscountBase", "CommercialDiscountAssignment", "OrderDiscountSnapshot",
    "OrderStateBase", "OrderStatusHistory",
    "OrderPaymentBase", "OrderPayment",
]

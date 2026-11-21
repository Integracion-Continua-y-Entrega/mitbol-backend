"""Asociaciones comerciales explícitas y cálculo histórico, sin emitir promociones."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.discount import Discount
from app.models.order import Order
from app.models.product import Product


class CommercialDiscountBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class CommercialDiscountAssignment(CommercialDiscountBase):
    __tablename__ = "CommercialDiscountAssignments"
    __table_args__ = (CheckConstraint("conditions IS NULL OR jsonb_typeof(conditions) = 'object'",
                                    name="ck_CommercialDiscountAssignments_conditions"),)

    discount_id: Mapped[int] = mapped_column(ForeignKey(Discount.discount_id, ondelete="CASCADE"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey(Product.product_id, ondelete="CASCADE"), primary_key=True)
    # NULL o reglas incompletas nunca habilitan un descuento por defecto.
    conditions: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class OrderDiscountSnapshot(CommercialDiscountBase):
    __tablename__ = "OrderDiscountSnapshots"
    __table_args__ = (CheckConstraint("jsonb_typeof(calculation) = 'object'",
                                    name="ck_OrderDiscountSnapshots_calculation"),)

    order_id: Mapped[UUID] = mapped_column(ForeignKey(Order.order_id, ondelete="CASCADE"), primary_key=True)
    calculation: Mapped[dict] = mapped_column(JSONB)

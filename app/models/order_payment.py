"""Confirmación manual única e independiente del estado operativo del pedido."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, MetaData, Numeric, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import PersonalAccount
from app.models.order import Order


class OrderPaymentBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class OrderPayment(OrderPaymentBase):
    """Su existencia acredita una verificación manual; no representa una venta en Loyverse."""

    __tablename__ = "OrderPayments"
    __table_args__ = (
        CheckConstraint("amount > 0 AND amount < 'Infinity'::numeric", name="ck_OrderPayments_amount"),
        CheckConstraint("method IN ('cash', 'card', 'transfer', 'other')", name="ck_OrderPayments_method"),
    )

    payment_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey(Order.order_id, ondelete="RESTRICT"), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric)
    method: Mapped[str] = mapped_column(String(20))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    verified_by_account_id: Mapped[int] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))

"""Historial inmutable de estados; separado de los contratos anteriores a 0014."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, MetaData, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import PersonalAccount
from app.models.order import Order


class OrderStateBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class OrderStatusHistory(OrderStateBase):
    __tablename__ = "OrderStatusHistories"
    __table_args__ = (
        CheckConstraint("reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500", name="ck_OrderStatusHistories_reason"),
        Index("ix_OrderStatusHistories_order_created", "order_id", "created_at"),
    )

    status_history_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(ForeignKey(Order.order_id, ondelete="CASCADE"))
    previous_status: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50))
    actor_account_id: Mapped[int | None] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())

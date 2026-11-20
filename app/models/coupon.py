"""Cupones personales reales; sin emisión automática, reservas ni canjes."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, MetaData, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import PersonalAccount
from app.models.discount import Discount


class CouponBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class CustomerCoupon(CouponBase):
    __tablename__ = "CustomerCoupons"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_CustomerCoupons_name"),
        CheckConstraint("status IN ('available', 'reserved', 'redeemed', 'expired')", name="ck_CustomerCoupons_status"),
        CheckConstraint("starts_at < expires_at", name="ck_CustomerCoupons_validity"),
        CheckConstraint("conditions IS NULL OR jsonb_typeof(conditions) = 'object'", name="ck_CustomerCoupons_conditions"),
        Index("ix_CustomerCoupons_account_coupon", "account_id", "customer_coupon_id"),
    )

    customer_coupon_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    discount_id: Mapped[int | None] = mapped_column(ForeignKey(Discount.discount_id, ondelete="RESTRICT"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="available")
    # NULL = condiciones todavía no definidas. Nunca se interpretan como libre uso.
    conditions: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

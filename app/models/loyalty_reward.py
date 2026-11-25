"""Concesiones no recurrentes, independientes de futuras ediciones de la meta."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, MetaData, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import PersonalAccount
from app.models.coupon import CustomerCoupon
from app.models.loyalty_goal import LoyaltyGoal
from app.models.loyalty_visit import LoyaltyVisit


class LoyaltyRewardBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class LoyaltyReward(LoyaltyRewardBase):
    """Una concesión por cuenta y meta; el cupón conserva su ciclo de vida propio."""

    __tablename__ = "LoyaltyRewards"
    __table_args__ = (
        UniqueConstraint("account_id", "goal_id", name="uq_LoyaltyRewards_account_goal"),
        UniqueConstraint("customer_coupon_id", name="uq_LoyaltyRewards_coupon"),
        CheckConstraint("goal_version > 0", name="ck_LoyaltyRewards_version"),
        CheckConstraint("required_visits > 0 AND total_visits >= required_visits", name="ck_LoyaltyRewards_visits"),
        CheckConstraint("isfinite(awarded_at)", name="ck_LoyaltyRewards_awarded_at"),
        CheckConstraint("jsonb_typeof(goal_snapshot) = 'object'", name="ck_LoyaltyRewards_snapshot"),
    )

    reward_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[int] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))
    goal_id: Mapped[UUID] = mapped_column(ForeignKey(LoyaltyGoal.goal_id, ondelete="RESTRICT"))
    visit_id: Mapped[UUID] = mapped_column(ForeignKey(LoyaltyVisit.visit_id, ondelete="RESTRICT"))
    customer_coupon_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(CustomerCoupon.customer_coupon_id, ondelete="RESTRICT"))
    goal_version: Mapped[int] = mapped_column(Integer)
    required_visits: Mapped[int] = mapped_column(Integer)
    total_visits: Mapped[int] = mapped_column(BigInteger)
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    goal_snapshot: Mapped[dict] = mapped_column(JSONB)

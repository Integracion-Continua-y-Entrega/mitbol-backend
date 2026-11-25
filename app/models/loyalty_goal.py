"""Metas configurables; definir una regla no registra visitas ni emite cupones."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, MetaData, SmallInteger, String, Text, Uuid, false, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import PersonalAccount
from app.models.discount import Discount


class LoyaltyBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class LoyaltyGoal(LoyaltyBase):
    """Identidad estable para asociar posteriormente la recompensa concedida a cada cliente."""

    __tablename__ = "LoyaltyGoals"
    __table_args__ = (
        CheckConstraint("required_visits > 0", name="ck_LoyaltyGoals_visits"),
        CheckConstraint("valid_days > 0", name="ck_LoyaltyGoals_valid_days"),
        CheckConstraint("version > 0", name="ck_LoyaltyGoals_version"),
        CheckConstraint("length(btrim(level_name)) > 0", name="ck_LoyaltyGoals_level_name"),
        CheckConstraint("length(btrim(reward_name)) > 0", name="ck_LoyaltyGoals_reward_name"),
        CheckConstraint("jsonb_typeof(conditions) = 'object'", name="ck_LoyaltyGoals_conditions"),
    )

    goal_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    required_visits: Mapped[int] = mapped_column(Integer, unique=True)
    level_name: Mapped[str] = mapped_column(String(50))
    reward_name: Mapped[str] = mapped_column(String(100))
    reward_description: Mapped[str | None] = mapped_column(Text)
    discount_id: Mapped[int] = mapped_column(ForeignKey(Discount.discount_id, ondelete="RESTRICT"))
    valid_days: Mapped[int] = mapped_column(SmallInteger)
    conditions: Mapped[dict] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=false())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    updated_by_account_id: Mapped[int] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))

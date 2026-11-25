"""Evidencia durable de presencia validada; no depende de la vida del QR o de una sesión."""

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, MetaData, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import PersonalAccount


class LoyaltyVisitBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class LoyaltyVisit(LoyaltyVisitBase):
    """Una cuenta por día local y un único registro por credencial, aunque luego se renueve."""

    __tablename__ = "LoyaltyVisits"
    __table_args__ = (
        UniqueConstraint("account_id", "visited_on", name="uq_LoyaltyVisits_account_day"),
        UniqueConstraint("identification_hash", name="uq_LoyaltyVisits_identification_hash"),
        CheckConstraint("identification_hash ~ '^[0-9a-f]{64}$'", name="ck_LoyaltyVisits_identification_hash"),
        CheckConstraint("length(btrim(restaurant_timezone)) > 0", name="ck_LoyaltyVisits_timezone"),
        CheckConstraint("isfinite(registered_at)", name="ck_LoyaltyVisits_registered_at"),
        CheckConstraint("visited_on = CAST(timezone(restaurant_timezone, registered_at) AS DATE)", name="ck_LoyaltyVisits_local_date"),
    )

    visit_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[int] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))
    visited_on: Mapped[date] = mapped_column(Date)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    restaurant_timezone: Mapped[str] = mapped_column(String(64))
    registered_by_account_id: Mapped[int] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))
    identification_hash: Mapped[str] = mapped_column(String(64))

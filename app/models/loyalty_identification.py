"""Una identificación temporal por cuenta; la fila no sustituye al registro de visitas."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, MetaData, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import AccountSession, PersonalAccount


class LoyaltyIdentificationBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class LoyaltyIdentification(LoyaltyIdentificationBase):
    """Conserva únicamente hash, sesión emisora, vigencia y evidencia del uso de la credencial actual."""

    __tablename__ = "LoyaltyIdentifications"
    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_LoyaltyIdentifications_hash"),
        CheckConstraint("auth_version >= 0", name="ck_LoyaltyIdentifications_auth_version"),
        CheckConstraint("created_at < expires_at", name="ck_LoyaltyIdentifications_expiration"),
        CheckConstraint("(consumed_at IS NULL) = (consumed_by_account_id IS NULL)", name="ck_LoyaltyIdentifications_consumption"),
        CheckConstraint("consumed_at IS NULL OR (created_at <= consumed_at AND consumed_at < expires_at)",
                        name="ck_LoyaltyIdentifications_consumed_time"),
    )

    account_id: Mapped[int] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="CASCADE"),
                                          primary_key=True, autoincrement=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey(AccountSession.session_id, ondelete="CASCADE"))
    auth_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_by_account_id: Mapped[int | None] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))

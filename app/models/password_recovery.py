"""Un único acceso de recuperación vigente por cuenta; nunca el token en claro."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import Base
from app.models.auth import PersonalAccount


class PasswordRecoveryBase(DeclarativeBase):
    # Mantiene los contratos históricos de identidad (0004/0005) independientes.
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class PasswordResetToken(PasswordRecoveryBase):
    __tablename__ = "PasswordResetTokens"
    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_PasswordResetTokens_hash"),
        CheckConstraint("auth_version >= 0", name="ck_PasswordResetTokens_auth_version"),
        CheckConstraint("created_at < expires_at", name="ck_PasswordResetTokens_expiration"),
    )

    account_id: Mapped[int] = mapped_column(
        ForeignKey(PersonalAccount.account_id, ondelete="CASCADE"), primary_key=True, autoincrement=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    auth_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

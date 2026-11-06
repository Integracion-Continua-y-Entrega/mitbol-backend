"""Contadores efímeros persistentes; nunca contienen correo o IP en claro."""

from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.auth_base import AuthBase


class AuthRateLimitPolicy(AuthBase):
    __tablename__ = "AuthRateLimitPolicy"
    __table_args__ = (
        CheckConstraint("policy_id = 1", name="ck_AuthRateLimitPolicy_singleton"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_AuthRateLimitPolicy_fingerprint"),
    )
    policy_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    fingerprint: Mapped[str] = mapped_column(String(64))


class AuthRateLimit(AuthBase):
    __tablename__ = "AuthRateLimits"
    __table_args__ = (
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_AuthRateLimits_key"),
        CheckConstraint("attempts > 0", name="ck_AuthRateLimits_attempts"),
        CheckConstraint("expires_at > started_at", name="ck_AuthRateLimits_expiry"),
        Index("ix_AuthRateLimits_expires_at", "expires_at"),
    )
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer)

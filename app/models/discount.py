"""HU-009: descuento comercial sincronizado; no calcula beneficios."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Discount(Base):
    __tablename__ = "Discounts"

    discount_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loyverse_id: Mapped[str] = mapped_column(String(100), unique=True)

    name: Mapped[str] = mapped_column(String(100))
    discount_type: Mapped[str] = mapped_column(String(20))
    value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    scope: Mapped[str] = mapped_column(String(20))

    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

"""Categorías comerciales; el prototipo categories permanece en LegacyBase."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CommercialCategory(Base):
    __tablename__ = "Categories"

    category_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loyverse_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    products: Mapped[list["Product"]] = relationship(back_populates="category", passive_deletes="all")

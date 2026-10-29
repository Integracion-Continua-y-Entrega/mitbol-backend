"""Información general del producto y configuraciones locales de MITBOL."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product_variant import ProductVariant


class Product(Base):
    __tablename__ = "Products"

    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loyverse_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    is_available: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    is_featured: Mapped[bool] = mapped_column(Boolean, server_default=false())
    is_new: Mapped[bool] = mapped_column(Boolean, server_default=true())
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product", passive_deletes="all"
    )

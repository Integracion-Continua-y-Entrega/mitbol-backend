"""Opciones de un modificador y sus precios adicionales."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product_modifier import ProductModifier


class ProductComplement(Base):
    __tablename__ = "ProductComplements"

    product_complement_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_modifier_id: Mapped[int] = mapped_column(
        ForeignKey("ProductModifiers.product_modifier_id")
    )
    loyverse_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    price: Mapped[Decimal] = mapped_column(Numeric)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_order: Mapped[int] = mapped_column(Integer)

    modifier: Mapped[ProductModifier] = relationship(back_populates="complements")

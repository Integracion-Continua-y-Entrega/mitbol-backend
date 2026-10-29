"""Cada presentación comercial conserva su identidad y su propio precio."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.product_modifier_assignment import ProductModifierAssignment


class ProductVariant(Base):
    __tablename__ = "ProductVariants"

    product_variant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loyverse_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    price: Mapped[Decimal] = mapped_column(Numeric)
    is_available: Mapped[bool] = mapped_column(Boolean, server_default=true())
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    product_id: Mapped[int] = mapped_column(ForeignKey("Products.product_id"))

    product: Mapped[Product] = relationship(back_populates="variants")
    modifier_assignments: Mapped[list[ProductModifierAssignment]] = relationship(
        back_populates="variant", passive_deletes="all"
    )

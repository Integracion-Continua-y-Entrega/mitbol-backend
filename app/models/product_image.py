"""Fotografía local de una variante; el archivo se administra fuera del modelo."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product_variant import ProductVariant


class ProductImage(Base):
    __tablename__ = "ProductImages"

    product_image_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_variant_id: Mapped[int] = mapped_column(
        ForeignKey("ProductVariants.product_variant_id")
    )
    image_url: Mapped[str] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    variant: Mapped[ProductVariant] = relationship()

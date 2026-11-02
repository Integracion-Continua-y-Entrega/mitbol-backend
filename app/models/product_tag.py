"""Asociación única entre una variante comercial y una etiqueta local."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product_variant import ProductVariant
    from app.models.tag import Tag


class ProductTag(Base):
    __tablename__ = "ProductTags"

    product_variant_id: Mapped[int] = mapped_column(
        ForeignKey("ProductVariants.product_variant_id"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("Tags.tag_id"), primary_key=True)

    variant: Mapped[ProductVariant] = relationship()
    tag: Mapped[Tag] = relationship(back_populates="variant_assignments")

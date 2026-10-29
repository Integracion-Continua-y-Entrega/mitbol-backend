"""Relación existente entre una variante y un grupo de modificadores."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product_modifier import ProductModifier
    from app.models.product_variant import ProductVariant


class ProductModifierAssignment(Base):
    __tablename__ = "ProductModifierAssignments"

    product_variant_id: Mapped[int] = mapped_column(
        ForeignKey("ProductVariants.product_variant_id"), primary_key=True
    )
    product_modifier_id: Mapped[int] = mapped_column(
        ForeignKey("ProductModifiers.product_modifier_id"), primary_key=True
    )

    variant: Mapped[ProductVariant] = relationship(back_populates="modifier_assignments")
    modifier: Mapped[ProductModifier] = relationship(back_populates="variant_assignments")

"""Grupos comerciales de modificadores sincronizados desde Loyverse."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product_complement import ProductComplement
    from app.models.product_modifier_assignment import ProductModifierAssignment


class ProductModifier(Base):
    __tablename__ = "ProductModifiers"

    product_modifier_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loyverse_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    display_order: Mapped[int] = mapped_column(Integer)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    complements: Mapped[list[ProductComplement]] = relationship(
        back_populates="modifier", passive_deletes="all"
    )
    variant_assignments: Mapped[list[ProductModifierAssignment]] = relationship(
        back_populates="modifier", passive_deletes="all"
    )

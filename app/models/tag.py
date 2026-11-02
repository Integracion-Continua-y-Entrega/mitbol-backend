"""Etiqueta local con nombre canónico y fecha generada por PostgreSQL."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from unicodedata import normalize

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.product_tag import ProductTag


class Tag(Base):
    __tablename__ = "Tags"

    tag_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    variant_assignments: Mapped[list[ProductTag]] = relationship(
        back_populates="tag", passive_deletes="all"
    )

    @validates("name")
    def normalize_name(self, key: str, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("El nombre de la etiqueta debe ser texto.")
        name = normalize("NFC", " ".join(value.split()).casefold())
        if not name or len(name) > 150:
            raise ValueError("El nombre normalizado debe tener entre 1 y 150 caracteres.")
        return name

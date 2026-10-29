"""Metadatos aislados del prototipo, excluidos de las migraciones vigentes."""

from sqlalchemy.orm import DeclarativeBase


class LegacyBase(DeclarativeBase):
    pass

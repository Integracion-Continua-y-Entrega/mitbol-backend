"""Metadatos de identidad; el catálogo conserva su registro de ocho tablas."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.db.base import Base


class AuthBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))

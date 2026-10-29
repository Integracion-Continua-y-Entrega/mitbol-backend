from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.models.legacy_base import LegacyBase

class Category(LegacyBase):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    loyverse_id = Column(String, unique=True, index=True, nullable=False) # Mapping con POS
    name = Column(String, nullable=False)

    items = relationship("Item", back_populates="category")

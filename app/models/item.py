from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    loyverse_id = Column(String, unique=True, index=True, nullable=False) # Mapping con POS
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    
    # Llave foránea local hacia nuestra tabla categories
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True) 

    # --- ENRIQUECIMIENTO DE DATOS (No provienen de Loyverse) ---
    description = Column(String, nullable=True)
    image_url = Column(String, nullable=True)

    # --- ESTADOS OPERATIVOS PROPIOS ---
    is_sold_out = Column(Boolean, default=False)
    is_featured = Column(Boolean, default=False)

    category = relationship("Category", back_populates="items")
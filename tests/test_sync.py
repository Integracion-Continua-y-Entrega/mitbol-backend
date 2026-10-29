"""Regresión del prototipo aislado; no valida la nueva sincronización HU-008."""

import pytest
import respx
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.item import Item
from app.models.category import Category
from app.models.legacy_base import LegacyBase as Base
from app.services.sync_service import SyncService

# Configuración de base de datos en memoria para pruebas rápidas
engine = create_engine("sqlite:///:memory:")
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)

def test_upsert_preserves_local_data(db_session):
    # 1. Simular un producto ya existente con datos locales enriquecidos
    initial_item = Item(
        loyverse_id="12345",
        name="Hamburguesa Clásica",
        price=100.0,
        description="Carne de res con queso", # Dato local a proteger
        image_url="https://miservidor.com/img.jpg" # Dato local a proteger
    )
    db_session.add(initial_item)
    db_session.commit()

    # 2. Simular los datos entrantes desde Loyverse (Cambio de precio y nombre)
    mock_loyverse_items = [{
        "id": "12345",
        "item_name": "Hamburguesa Clásica Doble", # Nuevo nombre
        "price": 120.0,                           # Nuevo precio
        "category_id": None
    }]

    # 3. Ejecutar el servicio de sincronización
    SyncService.upsert_items(db_session, mock_loyverse_items)

    # 4. Validar los resultados
    updated_item = db_session.query(Item).filter_by(loyverse_id="12345").first()
    
    # Verificamos que los datos maestros se actualizaron
    assert updated_item.price == 120.0
    assert updated_item.name == "Hamburguesa Clásica Doble"
    
    # Verificamos que los datos locales sobrevivieron al Upsert
    assert updated_item.description == "Carne de res con queso"
    assert updated_item.image_url == "https://miservidor.com/img.jpg"

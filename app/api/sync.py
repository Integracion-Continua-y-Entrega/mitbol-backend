from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.services.loyverse_client import LoyverseAPIClient
from app.services.sync_service import SyncService

router = APIRouter(prefix="/api/v1/sync", tags=["Sincronización"])

@router.post("/loyverse")
async def sync_loyverse_catalog(db: Session = Depends(get_db)):
    # Nota: Aquí irá la validación RBAC (ej. Depends(get_current_admin))
    # Por ahora, dejamos la lógica de orquestación pura.
    
    client = LoyverseAPIClient()
    try:
        # 1. Consumir la API externa usando el ciclo paginado
        categories_data = await client.get_all_paginated("/categories", "categories")
        items_data = await client.get_all_paginated("/items", "items")
        
        # 2. Procesar el Upsert en PostgreSQL con las listas completas
        SyncService.upsert_categories(db, categories_data)
        SyncService.upsert_items(db, items_data)
        
        return {"status": "success", "message": "Catálogo de Loyverse sincronizado exitosamente"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error durante la sincronización: {str(e)}")
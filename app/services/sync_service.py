from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.models.item import Item
from app.models.category import Category

class SyncService:
    @staticmethod
    def upsert_categories(db: Session, categories_data: list[dict]):
        for cat_data in categories_data:
            # Creamos la sentencia de inserción
            stmt = insert(Category).values(
                loyverse_id=cat_data["id"],
                name=cat_data["name"]
            )
            
            # Estrategia UPSERT: Si hay conflicto en loyverse_id, actualizamos el nombre
            stmt = stmt.on_conflict_do_update(
                index_elements=['loyverse_id'],
                set_=dict(name=stmt.excluded.name)
            )
            db.execute(stmt)
        db.commit()

    @staticmethod
    def upsert_items(db: Session, items_data: list[dict]):
        for item_data in items_data:
            # 1. Búsqueda segura del Category ID
            local_category_id = None
            loyverse_cat_id = item_data.get("category_id")
            if loyverse_cat_id:
                category = db.query(Category).filter(Category.loyverse_id == loyverse_cat_id).first()
                if category:
                    local_category_id = category.id

            # 2. Extracción adaptativa del Nombre
            item_name = item_data.get("item_name") or item_data.get("name") or "Sin nombre"

            # 3. Extracción adaptativa del Precio
            price = 0.0
            if "price" in item_data:
                price = item_data["price"]
            elif "variants" in item_data and len(item_data["variants"]) > 0:
                # El secreto: Loyverse usa 'default_price'
                price = item_data["variants"][0].get("default_price", 0.0)

            # 4. Sentencia de inserción UPSERT
            stmt = insert(Item).values(
                loyverse_id=item_data["id"],
                name=item_name,
                price=price,
                category_id=local_category_id
            )
            
            stmt = stmt.on_conflict_do_update(
                index_elements=['loyverse_id'],
                set_=dict(
                    name=stmt.excluded.name,
                    price=stmt.excluded.price,
                    category_id=stmt.excluded.category_id
                )
            )
            db.execute(stmt)
        db.commit()
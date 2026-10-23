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
            # 1. Buscamos el ID de la categoría local usando el ID de categoría de Loyverse
            local_category_id = None
            if item_data.get("category_id"):
                category = db.query(Category).filter(Category.loyverse_id == item_data["category_id"]).first()
                if category:
                    local_category_id = category.id

            # 2. Sentencia de inserción del Item
            stmt = insert(Item).values(
                loyverse_id=item_data["id"],
                name=item_data["item_name"],
                price=item_data["price"], # Ajusta estos campos según la respuesta exacta de Loyverse
                category_id=local_category_id
            )
            
            # 3. Estrategia UPSERT para Items
            # ¡IMPORTANTE!: Solo actualizamos datos maestros. 
            # NO incluimos description, image_url, is_sold_out ni is_featured para que no se borren en cada sincronización.
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
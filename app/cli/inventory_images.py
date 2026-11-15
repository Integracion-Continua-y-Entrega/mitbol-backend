"""Inventario de fotos locales registradas: siempre simulación, sin escribir.

No carga .env, no contacta R2 y no ofrece --execute. La conexión y la raíz de
archivos son explícitas; solo el operador decide una transferencia posterior.
"""

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.connection import (
    DatabaseTargetError, cloud_target_from_environment, create_database_engine,
    local_database_url, validate_local_connection,
)
from app.models import ProductImage, ProductVariant
from app.services.enrichment_errors import EnrichmentError, StorageError
from app.services.image_storage import LocalImageStorage, MAX_IMAGE_BYTES, image_parts, validate_image


def inventory_images(db, root: Path) -> dict:
    storage = LocalImageStorage(root)
    rows = db.execute(select(
        ProductImage.product_image_id, ProductImage.product_variant_id,
        ProductVariant.product_id, ProductImage.image_url, ProductImage.display_order,
    ).outerjoin(ProductVariant, ProductVariant.product_variant_id == ProductImage.product_variant_id)
      .order_by(ProductImage.product_image_id))
    images = []
    for image_id, variant_id, product_id, url, order in rows:
        item = {"product_image_id": image_id, "product_variant_id": variant_id,
                "product_id": product_id, "image_url": url, "display_order": order}
        images.append(item)
        try:
            product, variant, _, extension = image_parts(url)
            if (int(product), int(variant)) != (product_id, variant_id):
                raise StorageError()
        except (StorageError, TypeError):
            item["status"] = "invalid_reference"
            continue
        try:
            stored = storage.open_image(url)
            with stored.stream as stream:
                if not 0 < stored.size <= MAX_IMAGE_BYTES:
                    item["status"] = "invalid_image"
                    continue
                data = stream.read(MAX_IMAGE_BYTES + 1)
            _, actual_extension = validate_image(data)
            if actual_extension != extension:
                item["status"] = "invalid_image"
                continue
        except FileNotFoundError:
            item["status"] = "missing"
            continue
        except EnrichmentError:
            item["status"] = "invalid_image"
            continue
        except OSError:
            item["status"] = "unreadable"
            continue
        item.update(status="recoverable", size=len(data), sha256=sha256(data).hexdigest())
    return {"dry_run": True, "writes": False, "r2_contacted": False,
            "counts": dict(Counter(image["status"] for image in images)), "images": images}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Siempre activo; no existe modo de escritura.")
    args = parser.parse_args(argv)
    engine = None
    try:
        value = os.getenv("DATABASE_URL")
        if not value or not args.local_root.is_dir():
            raise DatabaseTargetError("Se requieren DATABASE_URL de proceso y una raíz local existente explícita.")
        target = cloud_target_from_environment()
        url = target.validate_url(value) if target else local_database_url(value)
        if url.database != args.expected_database or (target and target.expected_database != args.expected_database):
            raise DatabaseTargetError("La base no coincide con --expected-database.")
        engine = create_database_engine(url, target=target, read_only=True)
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            if target:
                target.validate_connection(connection)
            else:
                validate_local_connection(connection)
            with Session(bind=connection) as db:
                report = inventory_images(db, args.local_root)
        print(json.dumps(report, ensure_ascii=True))
        return 0
    except (DatabaseTargetError, SQLAlchemyError, OSError, EnrichmentError):
        print(json.dumps({"status": "error", "writes": False,
                          "message": "No fue posible inventariar; revisa destino, permisos y raíz local."}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

"""Entrega pública de imágenes registradas de productos comercialmente activos."""

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.db.database import get_db
from app.models import Product, ProductImage, ProductVariant
from app.services.enrichment_errors import EnrichmentError, StorageError
from app.services.product_enrichment_service import database_errors

router = APIRouter(tags=["Fotografías públicas"])
Identifier = Annotated[int, Path(ge=1, le=2_147_483_647)]


@router.get(
    "/media/products/{product_id}/variants/{variant_id}/{filename}",
    summary="Consultar fotografía publicada", response_class=StreamingResponse,
    responses={200: {"content": {kind: {} for kind in ("image/jpeg", "image/png", "image/webp")}},
               404: {"description": "Fotografía no publicada o archivo no disponible."}},
)
def get_image(
    product_id: Identifier, variant_id: Identifier,
    filename: Annotated[str, Path(pattern=r"^[0-9a-f]{32}\.(jpg|png|webp)$")],
    request: Request, db: Session = Depends(get_db),
):
    image_url = f"/media/products/{product_id}/variants/{variant_id}/{filename}"
    with database_errors(db):
        image = db.scalar(select(ProductImage).join(
            ProductVariant, ProductVariant.product_variant_id == ProductImage.product_variant_id,
        ).join(Product, Product.product_id == ProductVariant.product_id).where(
            ProductImage.image_url == image_url,
            ProductImage.product_variant_id == variant_id,
            ProductVariant.product_id == product_id, Product.is_active.is_(True),
        ))
        if image is None:
            raise EnrichmentError(404, "Fotografía no encontrada.")
        try:
            stream = request.app.state.image_storage.path_for(image.image_url).open("rb")
        except FileNotFoundError:
            raise EnrichmentError(404, "Fotografía no encontrada.") from None
        except OSError:
            raise StorageError() from None
    size = os.fstat(stream.fileno()).st_size

    def chunks():
        try:
            while chunk := stream.read(65_536):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        chunks(), media_type={"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[filename.rsplit(".", 1)[1]],
        headers={"X-Content-Type-Options": "nosniff", "Content-Length": str(size),
                 "Content-Disposition": f'inline; filename="{filename}"'},
    )

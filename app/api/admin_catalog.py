"""Operaciones locales de HU-005, registradas sólo al habilitar administración."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.security import ADMIN_SECURITY_DESCRIPTION, require_local_admin
from app.db.database import get_db
from app.schemas.enrichment import (
    DescriptionResponse, DescriptionUpdate, ImageListResponse, ImageOrderUpdate,
    ImageResponse, TagCreate, TagListResponse, TagResponse,
)
from app.schemas.menu import MenuErrorResponse
from app.schemas.availability import (
    AvailabilityUpdate, ProductAvailabilityResponse, VariantAvailabilityResponse,
)
from app.services import product_enrichment_service as service
from app.services import product_availability_service as availability
from app.services.enrichment_errors import StorageError
from app.services.image_storage import MAX_IMAGE_BYTES

Identifier = Annotated[int, Path(ge=1, le=2_147_483_647, description="ID interno positivo.")]
Database = Annotated[Session, Depends(get_db)]
router = APIRouter(
    prefix="/api/admin", tags=["Administración local"],
    dependencies=[Depends(require_local_admin)],
    responses={code: {"model": MenuErrorResponse, "description": detail} for code, detail in {
        401: "Credencial administrativa ausente o inválida.",
        403: "Acceso remoto o desde otro origen no permitido.",
        404: "Recurso no encontrado.",
        409: "Duplicado o conflicto concurrente.",
        413: "Se supera el tamaño admitido.",
        415: "Formato real de archivo no admitido.",
        500: "Error interno sin detalles de infraestructura.",
        503: "Base de datos o almacenamiento temporalmente no disponible.",
    }.items()},
)


@router.get("/products/{product_id}/availability", response_model=ProductAvailabilityResponse, summary="Consultar disponibilidad del producto")
def get_product_availability(product_id: Identifier, db: Database):
    return availability.get_product_availability(db, product_id)


@router.patch("/products/{product_id}/availability", response_model=ProductAvailabilityResponse, summary="Actualizar disponibilidad local del producto", description="Modifica únicamente is_available; conserva el estado de sus variantes. Un producto comercialmente inactivo nunca puede pedirse. " + ADMIN_SECURITY_DESCRIPTION)
def update_product_availability(product_id: Identifier, payload: AvailabilityUpdate, db: Database):
    return availability.update_product_availability(db, product_id, payload.is_available)


@router.get("/variants/{variant_id}/availability", response_model=VariantAvailabilityResponse, summary="Consultar disponibilidad de la variante")
def get_variant_availability(variant_id: Identifier, db: Database):
    return availability.get_variant_availability(db, variant_id)


@router.patch("/variants/{variant_id}/availability", response_model=VariantAvailabilityResponse, summary="Actualizar disponibilidad local de la variante", description="Modifica únicamente is_available. Loyverse administra is_commercially_available; el producto padre también debe estar activo y disponible. " + ADMIN_SECURITY_DESCRIPTION)
def update_variant_availability(variant_id: Identifier, payload: AvailabilityUpdate, db: Database):
    return availability.update_variant_availability(db, variant_id, payload.is_available)


@router.get("/products/{product_id}/description", response_model=DescriptionResponse, summary="Consultar descripción local")
def get_description(product_id: Identifier, db: Database):
    return service.get_description(db, product_id)


@router.patch("/products/{product_id}/description", response_model=DescriptionResponse, summary="Actualizar o limpiar descripción", description="Texto plano de hasta 2,000 caracteres. null o texto vacío limpia el campo. " + ADMIN_SECURITY_DESCRIPTION)
def update_description(product_id: Identifier, payload: DescriptionUpdate, db: Database):
    return service.update_description(db, product_id, payload.description)


@router.get("/variants/{variant_id}/images", response_model=ImageListResponse, summary="Consultar fotografías de una variante")
def list_images(variant_id: Identifier, db: Database):
    return {"images": service.list_images(db, variant_id)}


@router.post("/variants/{variant_id}/images", response_model=ImageResponse, status_code=201, summary="Subir fotografía local", description="JPEG, PNG o WebP estático; máximo 5,000,000 bytes y 20 megapíxeles. Se valida y recodifica el contenido. El nombre original y MIME declarado no determinan el formato almacenado. Sin display_order se agrega al final. " + ADMIN_SECURITY_DESCRIPTION)
def add_image(
    variant_id: Identifier, request: Request, db: Database,
    file: Annotated[UploadFile, File(description="Archivo real JPEG, PNG o WebP, hasta 5 MB.")],
    display_order: Annotated[int | None, Form(ge=0, le=2_147_483_647)] = None,
):
    try:
        content = file.file.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise StorageError() from None
    return service.add_image(db, request.app.state.image_storage, variant_id, content, display_order)


@router.patch("/variants/{variant_id}/images/{image_id}", response_model=ImageResponse, summary="Cambiar orden de fotografía")
def update_image_order(variant_id: Identifier, image_id: Identifier, payload: ImageOrderUpdate, db: Database):
    return service.update_image_order(db, variant_id, image_id, payload.display_order)


@router.delete("/variants/{variant_id}/images/{image_id}", status_code=204, summary="Eliminar fotografía de una variante", description="Retira la referencia y el archivo mediante un diario privado. Si falla la limpieza posterior al commit, se conserva una operación pendiente y se reintenta en la siguiente carga o eliminación autorizada. Las URLs sin registro no se publican.")
def delete_image(variant_id: Identifier, image_id: Identifier, request: Request, db: Database):
    service.delete_image(db, request.app.state.image_storage, variant_id, image_id)
    return Response(status_code=204)


@router.get("/tags", response_model=TagListResponse, summary="Consultar etiquetas locales")
def list_tags(db: Database):
    return {"tags": service.list_tags(db)}


@router.post("/tags", response_model=TagResponse, status_code=201, summary="Crear etiqueta normalizada", description="Normaliza Unicode, mayúsculas y espacios. Entre 1 y 150 caracteres normalizados, sin HTML. Los duplicados devuelven 409.")
def create_tag(payload: TagCreate, db: Database):
    return service.create_tag(db, payload.name)


@router.get("/variants/{variant_id}/tags", response_model=TagListResponse, summary="Consultar etiquetas de una variante")
def list_variant_tags(variant_id: Identifier, db: Database):
    return {"tags": service.list_tags(db, variant_id)}


@router.put("/variants/{variant_id}/tags/{tag_id}", status_code=204, summary="Asociar etiqueta de forma idempotente")
def associate_tag(variant_id: Identifier, tag_id: Identifier, db: Database):
    service.associate_tag(db, variant_id, tag_id)
    return Response(status_code=204)


@router.delete("/variants/{variant_id}/tags/{tag_id}", status_code=204, summary="Retirar asociación de forma idempotente")
def remove_tag(variant_id: Identifier, tag_id: Identifier, db: Database):
    service.remove_tag(db, variant_id, tag_id)
    return Response(status_code=204)

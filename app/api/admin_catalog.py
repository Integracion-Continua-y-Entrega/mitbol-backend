"""Contratos existentes de HU-005/HU-007 para administración local protegida."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.security import ADMIN_SECURITY_DESCRIPTION, require_local_admin
from app.db.database import get_db
from app.api.auth_dependencies import get_authenticated_database, require_permission
from app.services.admin_transaction import audit_success
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


def create_admin_router(*, authenticated=False, images_enabled=True):
    """Un único contrato; los modos individual y legado nunca se mezclan."""
    security_description = (
        "Sesión individual local y permisos MITBOL. Las escrituras exigen Origin y X-CSRF-Token."
        if authenticated else ADMIN_SECURITY_DESCRIPTION
    )

    def permission(code):
        return [Depends(require_permission(code))] if authenticated else []

    Database = Annotated[Session, Depends(get_authenticated_database if authenticated else get_db)]
    router = APIRouter(
        prefix="/api/admin", tags=["Administración local"],
        dependencies=[Depends(require_permission("catalog:read") if authenticated else require_local_admin)],
        responses={code: {"model": MenuErrorResponse, "description": detail} for code, detail in {
            401: "Sesión o credencial administrativa ausente, inválida o expirada.",
            403: "Permiso, CSRF, origen o conexión local no autorizados.",
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


    @router.patch("/products/{product_id}/availability", dependencies=permission("availability:write"), response_model=ProductAvailabilityResponse, summary="Actualizar disponibilidad local del producto", description="Modifica únicamente is_available; conserva el estado de sus variantes. Un producto comercialmente inactivo nunca puede pedirse. " + security_description)
    def update_product_availability(product_id: Identifier, payload: AvailabilityUpdate, db: Database):
        result = availability.update_product_availability(db, product_id, payload.is_available)
        audit_success(db, "catalog.product.availability", "product", product_id)
        return result


    @router.get("/variants/{variant_id}/availability", response_model=VariantAvailabilityResponse, summary="Consultar disponibilidad de la variante")
    def get_variant_availability(variant_id: Identifier, db: Database):
        return availability.get_variant_availability(db, variant_id)


    @router.patch("/variants/{variant_id}/availability", dependencies=permission("availability:write"), response_model=VariantAvailabilityResponse, summary="Actualizar disponibilidad local de la variante", description="Modifica únicamente is_available. Loyverse administra is_commercially_available; el producto padre también debe estar activo y disponible. " + security_description)
    def update_variant_availability(variant_id: Identifier, payload: AvailabilityUpdate, db: Database):
        result = availability.update_variant_availability(db, variant_id, payload.is_available)
        audit_success(db, "catalog.variant.availability", "variant", variant_id)
        return result


    @router.get("/products/{product_id}/description", response_model=DescriptionResponse, summary="Consultar descripción local")
    def get_description(product_id: Identifier, db: Database):
        return service.get_description(db, product_id)


    @router.patch("/products/{product_id}/description", dependencies=permission("description:write"), response_model=DescriptionResponse, summary="Actualizar o limpiar descripción", description="Texto plano de hasta 2,000 caracteres. null o texto vacío limpia el campo. " + security_description)
    def update_description(product_id: Identifier, payload: DescriptionUpdate, db: Database):
        result = service.update_description(db, product_id, payload.description)
        audit_success(db, "catalog.product.description", "product", product_id)
        return result


    @router.get("/variants/{variant_id}/images", response_model=ImageListResponse, summary="Consultar fotografías de una variante")
    def list_images(variant_id: Identifier, db: Database):
        return {"images": service.list_images(db, variant_id)}


    @router.post("/variants/{variant_id}/images", dependencies=permission("images:write"), response_model=ImageResponse, status_code=201, summary="Subir fotografía local", description="JPEG, PNG o WebP estático; máximo 5,000,000 bytes y 20 megapíxeles. Se valida y recodifica el contenido. El nombre original y MIME declarado no determinan el formato almacenado. Sin display_order se agrega al final. " + security_description)
    def add_image(
        variant_id: Identifier, request: Request, db: Database,
        file: Annotated[UploadFile, File(description="Archivo real JPEG, PNG o WebP, hasta 5 MB.")],
        display_order: Annotated[int | None, Form(ge=0, le=2_147_483_647)] = None,
    ):
        try:
            content = file.file.read(MAX_IMAGE_BYTES + 1)
        except OSError:
            raise StorageError() from None
        result = service.add_image(db, request.app.state.image_storage, variant_id, content, display_order)
        audit_success(db, "catalog.image.create", "image", result.product_image_id)
        return result


    @router.patch("/variants/{variant_id}/images/{image_id}", dependencies=permission("images:write"), response_model=ImageResponse, summary="Cambiar orden de fotografía")
    def update_image_order(variant_id: Identifier, image_id: Identifier, payload: ImageOrderUpdate, db: Database):
        result = service.update_image_order(db, variant_id, image_id, payload.display_order)
        audit_success(db, "catalog.image.order", "image", image_id)
        return result


    @router.delete("/variants/{variant_id}/images/{image_id}", dependencies=permission("images:write"), status_code=204, summary="Eliminar fotografía de una variante", description="Retira la referencia y el archivo mediante un diario privado. Si falla la limpieza posterior al commit, se conserva una operación pendiente y se reintenta en la siguiente carga o eliminación autorizada. Las URLs sin registro no se publican.")
    def delete_image(variant_id: Identifier, image_id: Identifier, request: Request, db: Database):
        service.delete_image(db, request.app.state.image_storage, variant_id, image_id)
        audit_success(db, "catalog.image.delete", "image", image_id)
        return Response(status_code=204)


    @router.get("/tags", dependencies=permission("tags:write"), response_model=TagListResponse, summary="Consultar etiquetas locales")
    def list_tags(db: Database):
        return {"tags": service.list_tags(db)}


    @router.post("/tags", dependencies=permission("tags:write"), response_model=TagResponse, status_code=201, summary="Crear etiqueta normalizada", description="Normaliza Unicode, mayúsculas y espacios. Entre 1 y 150 caracteres normalizados, sin HTML. Los duplicados devuelven 409.")
    def create_tag(payload: TagCreate, db: Database):
        result = service.create_tag(db, payload.name)
        audit_success(db, "catalog.tag.create", "tag", result.tag_id)
        return result


    @router.get("/variants/{variant_id}/tags", dependencies=permission("tags:write"), response_model=TagListResponse, summary="Consultar etiquetas de una variante")
    def list_variant_tags(variant_id: Identifier, db: Database):
        return {"tags": service.list_tags(db, variant_id)}


    @router.put("/variants/{variant_id}/tags/{tag_id}", dependencies=permission("tags:write"), status_code=204, summary="Asociar etiqueta de forma idempotente")
    def associate_tag(variant_id: Identifier, tag_id: Identifier, db: Database):
        service.associate_tag(db, variant_id, tag_id)
        audit_success(db, "catalog.variant.tag.attach", "variant_tag", f"{variant_id}:{tag_id}")
        return Response(status_code=204)


    @router.delete("/variants/{variant_id}/tags/{tag_id}", dependencies=permission("tags:write"), status_code=204, summary="Retirar asociación de forma idempotente")
    def remove_tag(variant_id: Identifier, tag_id: Identifier, db: Database):
        service.remove_tag(db, variant_id, tag_id)
        audit_success(db, "catalog.variant.tag.remove", "variant_tag", f"{variant_id}:{tag_id}")
        return Response(status_code=204)

    if authenticated and not images_enabled:
        router.routes = [route for route in router.routes if route.endpoint not in {add_image, update_image_order, delete_image}]
    return router

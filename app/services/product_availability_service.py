"""Disponibilidad local transaccional, coordinada con la sincronización comercial."""

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload, selectinload

from app.models import Product, ProductVariant
from app.schemas.availability import ProductAvailabilityResponse, VariantAvailabilityResponse
from app.services.admin_transaction import commit_changes
from app.services.availability_rules import product_can_order, variant_can_order
from app.services.enrichment_errors import EnrichmentError
from app.services.product_enrichment_service import database_errors
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY


def _mutation_lock(db):
    # La misma clave que HU-008 evita sobrescrituras y respuestas inconsistentes
    # durante una baja/reactivación. Se libera al commit o rollback.
    if db.get_bind().dialect.name != "postgresql":
        raise EnrichmentError(500, "La administración de disponibilidad requiere PostgreSQL.")
    if not db.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
        raise EnrichmentError(409, "Hay otra operación de catálogo en curso; intenta nuevamente.")


def _product(db, product_id):
    product = db.scalar(select(Product).where(Product.product_id == product_id).options(
        selectinload(Product.variants),
    ))
    if product is None:
        raise EnrichmentError(404, "Producto no encontrado.")
    return product


def _variant(db, variant_id):
    variant = db.scalar(select(ProductVariant).where(
        ProductVariant.product_variant_id == variant_id,
    ).options(joinedload(ProductVariant.product)))
    if variant is None:
        raise EnrichmentError(404, "Variante no encontrada.")
    return variant


def _product_response(product):
    return ProductAvailabilityResponse(
        product_id=product.product_id, is_active=product.is_active,
        is_available=product.is_available, can_order=product_can_order(product),
    )


def _variant_response(variant):
    return VariantAvailabilityResponse(
        product_variant_id=variant.product_variant_id, product_id=variant.product_id,
        is_available=variant.is_available,
        is_commercially_available=variant.is_commercially_available,
        product_is_active=variant.product.is_active,
        product_is_available=variant.product.is_available,
        can_order=variant_can_order(variant.product, variant),
    )


def get_product_availability(db, product_id):
    with database_errors(db):
        return _product_response(_product(db, product_id))


def update_product_availability(db, product_id, is_available):
    with database_errors(db):
        _mutation_lock(db)
        product = _product(db, product_id)
        product.is_available = is_available
        db.flush()
        result = _product_response(product)
        commit_changes(db)
        return result


def get_variant_availability(db, variant_id):
    with database_errors(db):
        return _variant_response(_variant(db, variant_id))


def update_variant_availability(db, variant_id, is_available):
    with database_errors(db):
        _mutation_lock(db)
        variant = _variant(db, variant_id)
        variant.is_available = is_available
        db.flush()
        result = _variant_response(variant)
        commit_changes(db)
        return result

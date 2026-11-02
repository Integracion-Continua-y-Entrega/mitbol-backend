"""Administración local de HU-005 sin modificar campos comerciales de Loyverse."""

from contextlib import contextmanager
import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError, TimeoutError

from app.models import Product, ProductImage, ProductTag, ProductVariant, Tag
from app.schemas.enrichment import DescriptionResponse, ImageResponse, TagResponse
from app.services.enrichment_errors import EnrichmentError, StorageError
from app.services.image_storage import LocalImageStorage, validate_image

logger = logging.getLogger(__name__)
IMAGE_STORAGE_LOCK_KEY = 0x4855303035494D47


@contextmanager
def database_errors(db):
    try:
        yield
    except EnrichmentError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise EnrichmentError(409, "La operación entra en conflicto con los datos existentes.") from None
    except (OperationalError, InterfaceError, TimeoutError):
        db.rollback()
        raise EnrichmentError(503, "La base de datos no está disponible temporalmente.") from None
    except OSError:
        db.rollback()
        raise StorageError() from None
    except Exception as error:
        db.rollback()
        logger.error("Falló una operación de enriquecimiento (%s).", type(error).__name__)
        raise EnrichmentError(500, "No fue posible completar la operación.") from None


def require_product(db, product_id):
    product = db.get(Product, product_id)
    if product is None:
        raise EnrichmentError(404, "Producto no encontrado.")
    return product


def require_variant(db, variant_id, *, lock=False):
    statement = select(ProductVariant).where(ProductVariant.product_variant_id == variant_id)
    if lock:
        statement = statement.with_for_update()
    variant = db.scalar(statement)
    if variant is None:
        raise EnrichmentError(404, "Variante no encontrada.")
    return variant


def require_tag(db, tag_id):
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise EnrichmentError(404, "Etiqueta no encontrada.")
    return tag


def get_description(db, product_id):
    with database_errors(db):
        return DescriptionResponse.model_validate(require_product(db, product_id))


def update_description(db, product_id, description):
    with database_errors(db):
        product = require_product(db, product_id)
        product.description = description
        db.flush()
        result = DescriptionResponse.model_validate(product)
        db.commit()
        return result


def list_tags(db, variant_id=None):
    with database_errors(db):
        statement = select(Tag)
        if variant_id is not None:
            require_variant(db, variant_id)
            statement = statement.join(ProductTag).where(ProductTag.product_variant_id == variant_id)
        return [TagResponse.model_validate(tag) for tag in db.scalars(statement.order_by(Tag.tag_id))]


def create_tag(db, name):
    with database_errors(db):
        tag = Tag(name=name)
        db.add(tag)
        db.flush()
        result = TagResponse.model_validate(tag)
        db.commit()
        return result


def associate_tag(db, variant_id, tag_id):
    with database_errors(db):
        require_variant(db, variant_id)
        require_tag(db, tag_id)
        db.execute(insert(ProductTag).values(
            product_variant_id=variant_id, tag_id=tag_id,
        ).on_conflict_do_nothing(index_elements=["product_variant_id", "tag_id"]))
        db.commit()


def remove_tag(db, variant_id, tag_id):
    with database_errors(db):
        require_variant(db, variant_id)
        require_tag(db, tag_id)
        assignment = db.get(ProductTag, (variant_id, tag_id))
        if assignment is not None:
            db.delete(assignment)
        db.commit()


def list_images(db, variant_id):
    with database_errors(db):
        require_variant(db, variant_id)
        return [ImageResponse.model_validate(image) for image in db.scalars(
            select(ProductImage).where(ProductImage.product_variant_id == variant_id)
            .order_by(ProductImage.display_order, ProductImage.product_image_id)
        )]


def require_image(db, variant_id, image_id):
    require_variant(db, variant_id)
    image = db.scalar(select(ProductImage).where(
        ProductImage.product_image_id == image_id, ProductImage.product_variant_id == variant_id,
    ).with_for_update())
    if image is None:
        raise EnrichmentError(404, "Fotografía no encontrada en la variante.")
    return image


def _storage_lock(db):
    if db.get_bind().dialect.name != "postgresql":
        raise EnrichmentError(500, "La administración de imágenes requiere PostgreSQL.")
    if not db.scalar(select(func.pg_try_advisory_xact_lock(IMAGE_STORAGE_LOCK_KEY))):
        raise EnrichmentError(409, "Hay otra operación de imágenes en curso; intenta nuevamente.")


def _recover_failed(db, storage):
    try:
        db.rollback()
        _storage_lock(db)
        storage.recover(db)
    except Exception as error:
        # El diario permanece para reintentar en la siguiente mutación autorizada.
        logger.warning("Recuperación de imágenes pendiente (%s).", type(error).__name__)


def _finalize(storage, operation, referenced):
    try:
        storage.settle(operation, referenced)
    except (OSError, StorageError) as error:
        logger.warning("Limpieza de imágenes pendiente (%s).", type(error).__name__)


def add_image(db, storage: LocalImageStorage, variant_id, content, display_order=None):
    processed, extension = validate_image(content)
    with database_errors(db):
        _storage_lock(db)
        storage.recover(db)
        variant = require_variant(db, variant_id, lock=True)
        if display_order is None:
            maximum = db.scalar(select(func.max(ProductImage.display_order)).where(
                ProductImage.product_variant_id == variant_id,
            ))
            display_order = 0 if maximum is None else maximum + 1
            if display_order > 2_147_483_647:
                raise EnrichmentError(409, "No hay un orden disponible al final; especifica display_order.")
        try:
            operation = storage.prepare_create(processed, extension, variant.product_id, variant_id)
            image = ProductImage(product_variant_id=variant_id, image_url=operation.image_url, display_order=display_order)
            db.add(image)
            db.flush()
            result = ImageResponse.model_validate(image)
            db.commit()
        except Exception:
            _recover_failed(db, storage)
            raise
        _finalize(storage, operation, True)
        return result


def update_image_order(db, variant_id, image_id, display_order):
    with database_errors(db):
        image = require_image(db, variant_id, image_id)
        image.display_order = display_order
        db.flush()
        result = ImageResponse.model_validate(image)
        db.commit()
        return result


def delete_image(db, storage: LocalImageStorage, variant_id, image_id):
    with database_errors(db):
        _storage_lock(db)
        storage.recover(db)
        image = require_image(db, variant_id, image_id)
        try:
            # Si otra fila referencia la misma URL, conserva el archivo compartido.
            shared = db.scalar(select(func.count()).select_from(ProductImage).where(
                ProductImage.image_url == image.image_url,
            )) > 1
            operation = None if shared else storage.prepare_delete(image.image_url)
            db.delete(image)
            db.flush()
            db.commit()
        except Exception:
            _recover_failed(db, storage)
            raise
        if operation is not None:
            _finalize(storage, operation, False)

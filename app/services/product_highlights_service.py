"""HU-006: consulta y edición de indicadores para la API administrativa."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.schemas.highlights import ProductHighlightsResponse
from app.services.admin_transaction import commit_changes
from app.services.enrichment_errors import EnrichmentError
from app.services.product_enrichment_service import database_errors, require_product
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY


def _lock_catalog(db: Session) -> None:
    """Coordina ediciones locales con sincronización, igual que HU-007."""
    if db.get_bind().dialect.name != "postgresql":
        raise EnrichmentError(500, "La administración de indicadores requiere PostgreSQL.")
    acquired = db.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY)))
    if not acquired:
        raise EnrichmentError(409, "Hay otra operación de catálogo en curso; intenta nuevamente.")


def get_product_highlights(db: Session, product_id: int) -> ProductHighlightsResponse:
    """Lee los indicadores almacenados, incluso si el producto está inactivo.

    Es una consulta administrativa, no una regla de publicación del menú.
    La sesión pertenece al llamador: no se crea, cierra ni confirma aquí.
    """
    with database_errors(db):
        product = require_product(db, product_id)

        return ProductHighlightsResponse(
            product_id=product.product_id,
            is_featured=product.is_featured,
            is_new=product.is_new,
        )


def update_product_highlights(
    db: Session,
    product_id: int,
    is_featured: bool,
    is_new: bool,
) -> ProductHighlightsResponse:
    """Reemplaza ambos indicadores sin modificar información comercial.

    Los booleanos se validarán en el schema del endpoint. En modo autenticado,
    la dependencia confirma cambio y auditoría dentro de la misma transacción.
    """
    with database_errors(db):
        _lock_catalog(db)
        product = require_product(db, product_id)

        product.is_featured = is_featured
        product.is_new = is_new

        db.flush()
        result = ProductHighlightsResponse(
            product_id=product.product_id,
            is_featured=product.is_featured,
            is_new=product.is_new,
        )
        commit_changes(db)
        return result

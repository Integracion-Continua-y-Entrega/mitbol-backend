"""HU-010: lectura consistente del resumen de las cinco entidades comerciales."""

from sqlalchemy import MetaData, literal, union_all
from sqlalchemy.orm import Session
from sqlalchemy.exc import ProgrammingError

from app.models import Base
from app.schemas.sync_status import EntitySyncSummary, SyncEntity
from app.services.sync_status_queries import build_sync_summary
from app.services.enrichment_errors import EnrichmentError


def get_sync_summaries(db: Session) -> list[EntitySyncSummary]:
    """Lee observaciones en una única sentencia, sin cerrar o confirmar la sesión.

    UNION ALL evita leer cada entidad en un snapshot distinto bajo READ COMMITTED.
    La integración con permisos y tratamiento de errores se hará en el router.
    No deduce actividad, éxito o historial a partir de estas fechas.
    """
    statements = []
    for entity in SyncEntity:
        table = Base.metadata.tables[entity.value].to_metadata(MetaData(), schema="public")
        statements.append(build_sync_summary(table).add_columns(literal(entity.value).label("entity")))
    statement = union_all(*statements).order_by("entity")

    try:
        rows = db.execute(statement).mappings().all()
    except ProgrammingError as error:
        if getattr(error.orig, "pgcode", None) in {"42P01", "42501"}:
            raise EnrichmentError(503, "La consulta de estado requiere esquema y permisos de lectura actualizados.") from None
        raise

    return [EntitySyncSummary.model_validate(row) for row in rows]

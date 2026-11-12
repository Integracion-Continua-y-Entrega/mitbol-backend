"""HU-009: construcción de UPSERT en preparación, sin ejecución SQL."""

from sqlalchemy import Table, case
from sqlalchemy.dialects.postgresql import Insert, insert


PERSISTENCE_COLUMNS = frozenset({
    "loyverse_id", "name", "discount_type", "value", "scope", "is_active", "synced_at",
})


def build_discount_upsert(table: Table, values: dict) -> Insert:
    """Construye una operación por identidad comercial; nunca confirma cambios.

    La tabla/valores provienen del esquema y transformador internos. El llamador
    gestionará destino, transacción y ejecución de un lote validado completo.
    """
    if table.name != "Discounts" or table.schema not in {None, "public"}:
        raise ValueError("El UPSERT requiere la tabla Discounts del esquema aprobado.")
    if set(values) != PERSISTENCE_COLUMNS:
        raise ValueError("El UPSERT requiere exactamente las columnas comerciales preparadas.")

    statement = insert(table).values(**values)

    # No degradar un alcance conocido cuando una actualización solo sabe 'unknown'.
    scope_update = case(
        (statement.excluded.scope == "unknown", table.c.scope),
        else_=statement.excluded.scope,
    )

    return statement.on_conflict_do_update(
        index_elements=[table.c.loyverse_id],
        set_=dict(
            name=statement.excluded.name,
            discount_type=statement.excluded.discount_type,
            value=statement.excluded.value,
            is_active=statement.excluded.is_active,
            synced_at=statement.excluded.synced_at,
            scope=scope_update,
        ),
    )

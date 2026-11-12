"""HU-010: consultas de fechas observadas; no representan historial de trabajos."""

from sqlalchemy import Select, Table, func, select


SYNC_STATUS_TABLES = frozenset({
    "Products", "ProductVariants", "ProductModifiers", "ProductComplements", "Discounts",
})


def build_sync_summary(table: Table) -> Select:
    """Resume todas las filas de una entidad, incluidas las inactivas.

    No ejecuta SQL, consulta Loyverse ni deduce éxito de una ejecución.
    En una tabla vacía esperamos conteos cero y una fecha nula.
    """
    if table.name not in SYNC_STATUS_TABLES or table.schema not in {None, "public"}:
        raise ValueError("La entidad no pertenece al estado comercial de sincronización.")
    if "synced_at" not in table.c:
        raise ValueError("La entidad requiere la columna synced_at.")

    return select(
        func.count().label("total_count"),
        func.count(table.c.synced_at).label("synced_count"),
        func.max(table.c.synced_at).label("last_synced_at"),
    ).select_from(table)

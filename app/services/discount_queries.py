"""Construcción de consultas administrativas; sin ejecución ni rutas activas."""

from sqlalchemy import Select, Table, select


def build_discount_list(table: Table, *, limit: int = 50, offset: int = 0) -> Select:
    """Lista filas locales, incluidas las inactivas, con orden estable por ID.

    Los límites se aplican aquí y más adelante también en los parámetros HTTP.
    No consulta Loyverse ni calcula elegibilidad para promociones.
    """
    if table.name != "Discounts" or table.schema not in {None, "public"}:
        raise ValueError("La consulta requiere la tabla Discounts del esquema aprobado.")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit debe ser un entero entre 1 y 100.")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset debe ser un entero no negativo.")

    return select(table).order_by(table.c.discount_id.asc()).limit(limit).offset(offset)

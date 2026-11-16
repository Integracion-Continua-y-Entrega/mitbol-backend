"""Preflight comercial compartido; consulta privilegios sin ejecutar DML."""

from sqlalchemy import text

from app.db.connection import DatabaseTargetError
from app.models import catalog_metadata, runtime_catalog_metadata


WRITE_TABLES = {
    "Products": "product_id", "ProductVariants": "product_variant_id",
    "ProductModifiers": "product_modifier_id", "ProductComplements": "product_complement_id",
}

# Columnas que realmente usa SyncService. Los UPDATE comerciales no autorizan
# editar enriquecimientos, IDs originales, padres ni disponibilidad local.
WRITE_COLUMNS = {
    "Products": {"INSERT": ("loyverse_id", "name", "is_active", "synced_at"),
                 "UPDATE": ("name", "is_active", "synced_at")},
    "ProductVariants": {"INSERT": ("loyverse_id", "name", "price", "product_id", "is_available",
                                   "is_commercially_available", "synced_at"),
                        "UPDATE": ("name", "price", "is_commercially_available", "synced_at")},
    "ProductModifiers": {"INSERT": ("loyverse_id", "name", "display_order", "synced_at"),
                         "UPDATE": ("name", "display_order", "synced_at")},
    "ProductComplements": {"INSERT": ("loyverse_id", "name", "price", "display_order",
                                      "product_modifier_id", "is_active", "synced_at"),
                           "UPDATE": ("name", "price", "display_order", "is_active", "synced_at")},
    "ProductModifierAssignments": {"INSERT": ("product_variant_id", "product_modifier_id")},
}


def inspect_catalog_operator(connection) -> dict:
    """Comprueba capacidad sin probar DML ni consumir secuencias."""
    user, session_user, default_read_only, recovery = connection.execute(text(
        "SELECT current_user, session_user, current_setting('default_transaction_read_only'), "
        "pg_catalog.pg_is_in_recovery()"
    )).one()
    driver = connection.connection.driver_connection
    if (user != session_user or user != connection.engine.url.username
            or user == "mitbol_render_reader"):
        raise DatabaseTargetError("La sincronización requiere la identidad operativa de la conexión, no el lector.")
    if default_read_only != "off" or driver.readonly is True or recovery:
        raise DatabaseTargetError("La conexión operativa hereda solo lectura o apunta a una réplica.")
    if not connection.scalar(text(
        "SELECT pg_catalog.has_database_privilege(current_user, current_database(), 'CONNECT') "
        "AND pg_catalog.has_schema_privilege(current_user, 'public', 'USAGE')"
    )):
        raise DatabaseTargetError("Falta CONNECT o USAGE para el catálogo autorizado.")
    from app.services.sync_service import CATEGORIES_REVISION, validate_catalog_revision
    categories_enabled = validate_catalog_revision(connection) == CATEGORIES_REVISION
    metadata = runtime_catalog_metadata if categories_enabled else catalog_metadata
    for name in (*metadata.tables, "alembic_version"):
        relation = f'public."{name}"'
        valid = connection.scalar(text(
            "SELECT c.relkind = 'r' AND NOT c.relrowsecurity "
            "AND c.oid = pg_catalog.to_regclass(:unqualified) "
            "AND pg_catalog.has_table_privilege(current_user, c.oid, 'SELECT') "
            "FROM pg_catalog.pg_class c WHERE c.oid = pg_catalog.to_regclass(:relation)"
        ), {"relation": relation, "unqualified": f'"{name}"'})
        if not valid:
            raise DatabaseTargetError("Falta una tabla legible en public, hay RLS o search_path desvía el catálogo.")
        operations = dict(WRITE_COLUMNS.get(name, {}))
        if categories_enabled and name == "Products":
            operations = {key: (*values, "category_id") for key, values in operations.items()}
        if name == "Categories":
            operations = {"INSERT": ("loyverse_id", "name", "color", "is_active", "synced_at"),
                          "UPDATE": ("name", "color", "is_active", "synced_at")}
        for privilege, columns in operations.items():
            for column in columns:
                if not connection.scalar(text(
                    "SELECT pg_catalog.has_column_privilege(current_user, :relation, :column, :privilege)"
                ), {"relation": relation, "column": column, "privilege": privilege}):
                    raise DatabaseTargetError("Faltan permisos de columnas para las operaciones comerciales previstas.")
        privileges = ("DELETE",) if name == "ProductModifierAssignments" else ()
        for privilege in privileges:
            if not connection.scalar(text(
                "SELECT pg_catalog.has_table_privilege(current_user, :relation, :privilege)"
            ), {"relation": relation, "privilege": privilege}):
                raise DatabaseTargetError("Faltan permisos para las operaciones comerciales previstas.")
        if name in WRITE_TABLES or name == "Categories":
            if not connection.scalar(text(
                "SELECT pg_catalog.has_sequence_privilege(current_user, "
                "pg_catalog.pg_get_serial_sequence(:relation, :column), 'USAGE,UPDATE')"
            ), {"relation": relation, "column": WRITE_TABLES.get(name, "category_id")}):
                raise DatabaseTargetError("Falta acceso a una secuencia comercial; no se consumió ningún valor.")
    return {"user": user, "default_transaction_read_only": default_read_only,
            "write_permissions_verified": True}

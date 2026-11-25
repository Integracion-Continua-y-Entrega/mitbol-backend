"""Contratos por revisión. Sólo consulta catálogos del sistema PostgreSQL."""

import re

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from app.db.connection import DatabaseTargetError


RECENT_REVISIONS = tuple(f"20261119_{number:04d}" for number in range(9, 13))
CATEGORY_REVISIONS = frozenset({"20261115_0008", *RECENT_REVISIONS})
COMMERCIAL_DISCOUNTS_REVISION = "20261120_0013"
ORDER_MANAGEMENT_REVISION = "20261121_0014"
ORDER_PAYMENTS_REVISION = "20261124_0015"
LOYALTY_REVISION = "20261124_0016"
LATEST_REVISIONS = {COMMERCIAL_DISCOUNTS_REVISION, ORDER_MANAGEMENT_REVISION, ORDER_PAYMENTS_REVISION, LOYALTY_REVISION}
CATEGORY_REVISIONS = CATEGORY_REVISIONS | LATEST_REVISIONS
BRIDGE_REVISIONS = frozenset({"20261114_0007", *CATEGORY_REVISIONS})


def _type(value):
    return re.sub(r"\s+", "", value.lower().replace("varchar", "character varying"))


def validate_bridge_schema(connection):
    """Valida revisión, columnas, claves e índices mediante la misma conexión.

    Admite Connection SQLAlchemy o conexión DBAPI ya contrastada por el cliente.
    Conserva los perfiles históricos y agrega únicamente las entidades de cada
    revisión. No necesita SELECT sobre datos personales ni registra otro ORM.
    """
    driver = (connection.connection.driver_connection
              if hasattr(connection, "engine") else connection)
    from app.models import AuthBase, pre_categories_metadata
    from app.models.sync_run import SyncBase
    from app.models.order import OrderBase
    from app.models.password_recovery import PasswordRecoveryBase
    from app.models.coupon import CouponBase
    from app.models.commercial_discount import CommercialDiscountBase
    from app.models.order_status_history import OrderStateBase
    from app.models.order_payment import OrderPaymentBase
    from app.models.loyalty_goal import LoyaltyBase

    tables = [table for metadata in (pre_categories_metadata, AuthBase.metadata, SyncBase.metadata)
              for table in metadata.tables.values()]
    with driver.cursor() as cursor:
        cursor.execute("SELECT has_column_privilege(current_user, 'public.alembic_version', 'version_num', 'SELECT')")
        if not cursor.fetchone()[0]:
            raise DatabaseTargetError("El perfil puente requiere lectura de la revisión verificada.")
        cursor.execute("SELECT version_num FROM public.alembic_version")
        heads = cursor.fetchall()
        if len(heads) != 1 or heads[0][0] not in BRIDGE_REVISIONS:
            raise DatabaseTargetError("El backend requiere exactamente una revisión aprobada: 20261114_0007, 20261115_0008, 20261119_0009–0012, 20261120_0013, 20261121_0014 o 20261124_0015–0016.")
        revision = heads[0][0]
        if revision in RECENT_REVISIONS or revision in LATEST_REVISIONS:
            tables.extend(OrderBase.metadata.tables.values())
        if revision in RECENT_REVISIONS[2:] or revision in LATEST_REVISIONS:
            tables.extend(PasswordRecoveryBase.metadata.tables.values())
        if revision == RECENT_REVISIONS[3] or revision in LATEST_REVISIONS:
            tables.extend(CouponBase.metadata.tables.values())
        if revision in LATEST_REVISIONS:
            tables.extend(CommercialDiscountBase.metadata.tables.values())
        if revision in {ORDER_MANAGEMENT_REVISION, ORDER_PAYMENTS_REVISION, LOYALTY_REVISION}:
            tables.extend(OrderStateBase.metadata.tables.values())
        if revision in {ORDER_PAYMENTS_REVISION, LOYALTY_REVISION}:
            tables.extend(OrderPaymentBase.metadata.tables.values())
        if revision == LOYALTY_REVISION:
            tables.extend(LoyaltyBase.metadata.tables.values())
        expected = {(table.name, col.name): (_type(str(col.type.compile(dialect=postgresql.dialect()))), not col.nullable)
                    for table in tables for col in table.columns}
        if revision == RECENT_REVISIONS[0]:
            del expected[("Orders", "guest_tracking_token")]
        expected[("alembic_version", "version_num")] = ("charactervarying(32)", True)
        if revision in CATEGORY_REVISIONS:
            expected.update({("Categories", name): (_type(kind), required) for name, kind, required in (
                ("category_id", "integer", True), ("loyverse_id", "varchar(100)", True),
                ("name", "varchar(64)", True), ("color", "varchar(20)", False),
                ("is_active", "boolean", True), ("synced_at", "timestamp with time zone", True))})
            expected[("Products", "category_id")] = ("integer", False)
        cursor.execute("""SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            c.relrowsecurity OR c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped""")
        actual = {}
        for table, name, kind, required, rls in cursor.fetchall():
            if rls:
                raise DatabaseTargetError("El esquema puente no admite RLS inesperado.")
            actual[(table, name)] = (_type(kind), required)
        if actual != expected:
            raise DatabaseTargetError("La estructura de columnas no corresponde al contrato puente aprobado.")
        cursor.execute("""SELECT c.relname,x.conname,x.contype,x.convalidated,x.conenforced,
            ARRAY(SELECT a.attname FROM unnest(x.conkey) WITH ORDINALITY k(num,pos)
                  JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num ORDER BY k.pos),
            r.relname,x.confdeltype,
            ARRAY(SELECT a.attname FROM unnest(x.confkey) WITH ORDINALITY k(num,pos)
                  JOIN pg_attribute a ON a.attrelid=r.oid AND a.attnum=k.num ORDER BY k.pos)
            FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_class r ON r.oid=x.confrelid
            WHERE n.nspname='public'""")
        constraints = {(r[0], r[1]): r[2:] for r in cursor.fetchall()}
        delete_codes = {None: "a", "NO ACTION": "a", "RESTRICT": "r", "CASCADE": "c", "SET NULL": "n", "SET DEFAULT": "d"}
        for table in tables:
            for constraint in table.constraints:
                if revision == RECENT_REVISIONS[0] and table.name == "Orders" and constraint.name in {
                    "uq_Orders_guest_tracking_token", "ck_Orders_guest_tracking_token",
                }:
                    continue
                row = constraints.get((table.name, str(constraint.name)))
                kind = ("f" if isinstance(constraint, ForeignKeyConstraint) else
                        "p" if isinstance(constraint, PrimaryKeyConstraint) else
                        "u" if isinstance(constraint, UniqueConstraint) else
                        "c" if isinstance(constraint, CheckConstraint) else None)
                if kind is None:
                    continue
                if row is None or row[0] != kind or not row[1] or not row[2]:
                    raise DatabaseTargetError("Falta una restricción validada del esquema puente.")
                if kind != "c" and list(row[3]) != list(constraint.columns.keys()):
                    raise DatabaseTargetError("Una clave del esquema puente no corresponde al modelo aprobado.")
                if kind == "f" and (row[4] != constraint.referred_table.name
                        or row[5] != delete_codes[constraint.ondelete]
                        or list(row[6]) != [fk.column.name for fk in constraint.elements]):
                    raise DatabaseTargetError("Una relación del esquema puente es incompatible.")
        cursor.execute("""SELECT c.relname,i.relname,x.indisvalid AND x.indisready,x.indisunique,
            ARRAY(SELECT a.attname FROM unnest(x.indkey) WITH ORDINALITY k(num,pos)
                  JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num ORDER BY k.pos)
            FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'""")
        indexes = {(r[0], r[1]): r[2:] for r in cursor.fetchall()}
        for table in tables:
            for index in table.indexes:
                if indexes.get((table.name, index.name)) != (True, bool(index.unique), list(index.columns.keys())):
                    raise DatabaseTargetError("Falta un índice válido del esquema puente.")
        if revision in CATEGORY_REVISIONS:
            fk = constraints.get(("Products", "fk_Products_category_id"))
            if (fk != ("f", True, True, ["category_id"], "Categories", "r", ["category_id"])
                    or constraints.get(("Categories", "pk_Categories"), ())[:4] != ("p", True, True, ["category_id"])
                    or constraints.get(("Categories", "uq_Categories_loyverse_id"), ())[:4] != ("u", True, True, ["loyverse_id"])
                    or indexes.get(("Products", "ix_Products_category_id")) != (True, False, ["category_id"])):
                raise DatabaseTargetError("La extensión 0008 no corresponde al contrato aditivo aprobado.")
    return revision

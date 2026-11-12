"""Persistencia de HU-008. Recibe sesiones explícitas; no usa la conexión global."""

from contextlib import contextmanager
import re
from typing import Callable

from alembic.runtime.migration import MigrationContext
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.connection import (
    CloudDatabaseTarget, DatabaseTargetError, environment_mode, validate_local_connection,
)
from app.models import (
    Product, ProductComplement, ProductModifier, ProductModifierAssignment, ProductVariant,
)
from app.services.catalog_errors import (
    CatalogPersistenceError, CatalogReferenceError, CatalogSyncBusyError,
    CatalogTargetError, CatalogValidationError,
)
from app.services.catalog_probe import CatalogSnapshot, fetch_catalog
from app.services.catalog_transform import CatalogPlan, transform_catalog
from app.services.loyverse_client import LoyverseAPIClient
from app.services.sync_activity import mark_sync_execution

# Un catálogo por base: todas las instancias del servicio usan la misma clave.
CATALOG_SYNC_LOCK_KEY = 0x4D4954424F4C
CATALOG_SCHEMA_REVISION = "20261102_0003"
CATALOG_COMPATIBLE_REVISIONS = frozenset({CATALOG_SCHEMA_REVISION, "20261105_0004", "20261106_0005", "20261111_0006"})


def validate_catalog_revision(connection) -> str:
    heads = MigrationContext.configure(connection, opts={"version_table_schema": "public"}).get_current_heads()
    if len(heads) != 1 or heads[0] not in CATALOG_COMPATIBLE_REVISIONS:
        raise CatalogTargetError("La sincronización requiere 20261102_0003 o una revisión compatible hasta 20261111_0006.")
    return heads[0]


class SyncService:
    def __init__(
        self, session_factory: Callable[[], Session], *, expected_database: str, store_id: str,
        cloud_target: CloudDatabaseTarget | None = None,
        before_write: Callable[[Session, CatalogSnapshot, CatalogPlan], None] | None = None,
    ):
        if cloud_target is not None:
            if expected_database != cloud_target.expected_database:
                raise CatalogTargetError("La base de sincronización no coincide con la autorización cloud.")
        elif environment_mode() == "cloud":
            raise CatalogTargetError("La sincronización cloud requiere autorización explícita de host y base.")
        elif expected_database not in {"mitbol_dev", "mitbol_auth_local"} and not re.fullmatch(
            r"mitbol_test_(?:hu008_[0-9a-f]{12}|auth_[0-9a-f]{32})", expected_database,
        ):
            raise CatalogTargetError("El destino no es mitbol_dev, mitbol_auth_local o una base aislada de pruebas.")
        if not store_id or not store_id.strip():
            raise CatalogValidationError("Se requiere una tienda explícita para sincronizar.")
        self.session_factory = session_factory
        self.expected_database = expected_database
        self.store_id = store_id
        self.cloud_target = cloud_target
        self.before_write = before_write

    @contextmanager
    def _transaction(self):
        try:
            with self.session_factory() as session, session.begin():
                if session.get_bind().dialect.name != "postgresql":
                    raise CatalogTargetError("La sincronización requiere PostgreSQL.")
                if self.cloud_target is not None:
                    self.cloud_target.validate_connection(session.connection())
                    validate_catalog_revision(session.connection())
                else:
                    validate_local_connection(session.connection())
                actual_database = session.scalar(select(func.current_database()))
                if actual_database != self.expected_database:
                    raise CatalogTargetError("La conexión real no coincide con la base autorizada.")
                if not session.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY))):
                    raise CatalogSyncBusyError("Ya hay una sincronización comercial en ejecución.")
                if not mark_sync_execution(session):
                    raise CatalogSyncBusyError("Ya hay una ejecución de sincronización activa.")
                yield session
        except DatabaseTargetError as error:
            raise CatalogTargetError(str(error)) from None
        except SQLAlchemyError as error:
            raise CatalogPersistenceError("Falló la persistencia; verifica el estado antes de repetir la operación.") from error

    def _plan(self, snapshot: CatalogSnapshot) -> CatalogPlan:
        if snapshot.store.get("id") != self.store_id:
            raise CatalogValidationError("El catálogo recibido corresponde a otra tienda.")
        return transform_catalog(snapshot)

    def persist_snapshot(self, snapshot: CatalogSnapshot) -> dict:
        """Entrada para snapshots validados y pruebas; no realiza llamadas HTTP."""
        with self._transaction() as session:
            result = self._validated_write(session, snapshot)
        return result

    async def synchronize(self, client: LoyverseAPIClient) -> dict:
        """Mantiene el bloqueo desde antes de leer Loyverse hasta el commit.

        Así dos ejecuciones no descargan y aplican catálogos en orden invertido.
        El destino y la tienda se comprueban antes de persistir el catálogo.
        """
        with self._transaction() as session:
            snapshot = await fetch_catalog(client, self.store_id, include_deleted=True)
            result = self._validated_write(session, snapshot)
        return result

    def _validated_write(self, session: Session, snapshot: CatalogSnapshot) -> dict:
        plan = self._plan(snapshot)
        if self.before_write is not None:
            self.before_write(session, snapshot, plan)
        return self._write(session, plan)

    @staticmethod
    def _upsert(session: Session, model, values: dict, fields: tuple[str, ...], *, parent=None) -> int:
        table = model.__table__
        statement = insert(table).values(**values)
        options = {
            "index_elements": [table.c.loyverse_id],
            "set_": {field: statement.excluded[field] for field in fields},
        }
        if parent:
            # Nunca reasignar una identidad histórica a otro producto o grupo.
            options["where"] = table.c[parent] == statement.excluded[parent]
        statement = statement.on_conflict_do_update(**options)
        primary_key = next(iter(table.primary_key.columns))
        identifier = session.execute(statement.returning(primary_key)).scalar_one_or_none()
        if identifier is None:
            raise CatalogReferenceError("Un identificador existente cambió de producto o grupo padre.")
        return identifier

    def _write(self, session: Session, plan: CatalogPlan) -> dict:
        products = {}
        modifiers = {}
        variants = {}
        for product in plan.products:
            products[product.loyverse_id] = self._upsert(session, Product, {
                "loyverse_id": product.loyverse_id, "name": product.name,
                "is_active": True, "synced_at": plan.synced_at,
            }, ("name", "is_active", "synced_at"))
        for modifier in plan.modifiers:
            modifiers[modifier.loyverse_id] = self._upsert(session, ProductModifier, {
                "loyverse_id": modifier.loyverse_id, "name": modifier.name,
                "display_order": modifier.display_order, "synced_at": plan.synced_at,
            }, ("name", "display_order", "synced_at"))
        for variant in plan.variants:
            # Nuevas: preferencia local disponible y estado comercial verificado.
            # Existentes: Loyverse nunca modifica la preferencia local.
            variants[variant.loyverse_id] = self._upsert(session, ProductVariant, {
                "loyverse_id": variant.loyverse_id, "name": variant.name, "price": variant.price,
                "product_id": products[variant.product_loyverse_id],
                "is_available": True,
                "is_commercially_available": variant.is_commercially_available,
                "synced_at": plan.synced_at,
            }, ("name", "price", "is_commercially_available", "synced_at"), parent="product_id")
        for complement in plan.complements:
            self._upsert(session, ProductComplement, {
                "loyverse_id": complement.loyverse_id, "name": complement.name,
                "price": complement.price, "display_order": complement.display_order,
                "product_modifier_id": modifiers[complement.modifier_loyverse_id],
                "is_active": True, "synced_at": plan.synced_at,
            }, ("name", "price", "display_order", "is_active", "synced_at"), parent="product_modifier_id")

        desired = {identifier: set() for identifier in variants}
        for variant_id, modifier_id in plan.assignments:
            desired[variant_id].add(modifiers[modifier_id])
        removed = 0
        inserted = 0
        for external_id, internal_id in variants.items():
            statement = delete(ProductModifierAssignment).where(
                ProductModifierAssignment.product_variant_id == internal_id,
            )
            if desired[external_id]:
                statement = statement.where(
                    ProductModifierAssignment.product_modifier_id.not_in(desired[external_id]),
                )
            removed += session.execute(statement).rowcount
            for modifier_id in sorted(desired[external_id]):
                statement = insert(ProductModifierAssignment).values(
                    product_variant_id=internal_id, product_modifier_id=modifier_id,
                ).on_conflict_do_nothing(
                    index_elements=["product_variant_id", "product_modifier_id"],
                )
                inserted += session.execute(statement).rowcount

        retired, retirement_links = self._retire(session, plan)
        return {
            "products_processed": len(plan.products), "variants_processed": len(plan.variants),
            "modifiers_processed": len(plan.modifiers), "complements_processed": len(plan.complements),
            "assignments_inserted": inserted, "assignments_removed": removed + retirement_links,
            "retired": retired,
        }

    @staticmethod
    def _retire_child(session, model, parent, child_key, parent_key, external_id, parent_external_id, values):
        row = session.execute(select(
            getattr(model, child_key), parent.loyverse_id,
        ).join(parent, getattr(model, parent_key) == getattr(parent, parent_key)).where(
            model.loyverse_id == external_id,
        )).one_or_none()
        if row is None:
            return None
        if row[1] != parent_external_id:
            raise CatalogReferenceError("Una baja declara un padre diferente al registrado.")
        session.execute(update(model).where(getattr(model, child_key) == row[0]).values(**values))
        return row[0]

    def _retire(self, session: Session, plan: CatalogPlan) -> tuple[dict, int]:
        retired = {"products": 0, "variants": 0, "modifiers": 0, "complements": 0}
        removed = 0
        for external_id in plan.deleted_products:
            product_id = session.execute(update(Product).where(Product.loyverse_id == external_id).values(
                is_active=False, synced_at=plan.synced_at,
            ).returning(Product.product_id)).scalar_one_or_none()
            if product_id is not None:
                retired["products"] += 1
                variant_ids = select(ProductVariant.product_variant_id).where(ProductVariant.product_id == product_id)
                removed += session.execute(delete(ProductModifierAssignment).where(
                    ProductModifierAssignment.product_variant_id.in_(variant_ids),
                )).rowcount
                retired["variants"] += session.execute(update(ProductVariant).where(
                    ProductVariant.product_id == product_id,
                ).values(is_commercially_available=False, synced_at=plan.synced_at)).rowcount
        for external_id, parent_id in plan.deleted_variants:
            variant_id = self._retire_child(
                session, ProductVariant, Product, "product_variant_id", "product_id", external_id, parent_id,
                {"is_commercially_available": False, "synced_at": plan.synced_at},
            )
            if variant_id is not None:
                retired["variants"] += 1
                removed += session.execute(delete(ProductModifierAssignment).where(
                    ProductModifierAssignment.product_variant_id == variant_id,
                )).rowcount
        for external_id in plan.deleted_modifiers:
            modifier_id = session.execute(update(ProductModifier).where(
                ProductModifier.loyverse_id == external_id,
            ).values(synced_at=plan.synced_at).returning(ProductModifier.product_modifier_id)).scalar_one_or_none()
            if modifier_id is not None:
                retired["modifiers"] += 1
                removed += session.execute(delete(ProductModifierAssignment).where(
                    ProductModifierAssignment.product_modifier_id == modifier_id,
                )).rowcount
                retired["complements"] += session.execute(update(ProductComplement).where(
                    ProductComplement.product_modifier_id == modifier_id,
                ).values(is_active=False, synced_at=plan.synced_at)).rowcount
        for external_id, parent_id in plan.deleted_complements:
            complement_id = self._retire_child(
                session, ProductComplement, ProductModifier, "product_complement_id", "product_modifier_id",
                external_id, parent_id, {"is_active": False, "synced_at": plan.synced_at},
            )
            if complement_id is not None:
                retired["complements"] += 1
        return retired, removed

"""HU-005 sobre PostgreSQL aislado; nunca migra ni escribe en mitbol_dev."""

from app.models import catalog_metadata
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import MetaData, create_engine, delete, func, inspect, insert, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Base, Product, ProductImage, ProductTag, ProductVariant, Tag
from app.services.sync_service import SyncService
from app.services.catalog_transform import transform_catalog

INITIAL_REVISION = "20261029_0001"
ENRICHMENT_REVISION = "20261102_0002"
CATALOG_TABLES = {
    "Products", "ProductVariants", "ProductModifiers",
    "ProductComplements", "ProductModifierAssignments",
}


@pytest.fixture
def enrichment_catalog(sync_db, commercial_snapshot):
    sync_db.service.persist_snapshot(commercial_snapshot)
    with sync_db.sessions() as session:
        variant = session.scalar(select(ProductVariant).where(ProductVariant.loyverse_id == "single"))
        other = session.scalar(select(ProductVariant).where(ProductVariant.loyverse_id == "double"))
        tag = Tag(name="  PICANTE ")
        image = ProductImage(variant=variant, image_url="/media/test.webp", display_order=2)
        assignment = ProductTag(variant=variant, tag=tag)
        session.add_all([image, assignment])
        session.flush()
        yield SimpleNamespace(
            session=session, variant=variant, other=other, image=image,
            tag=tag, assignment=assignment,
        )


def test_migrated_postgresql_matches_all_models(sync_db):
    connection = sync_db.connection
    assert MigrationContext.configure(connection).get_current_heads() == ("20261102_0003",)
    inspector = inspect(connection)
    assert set(inspector.get_table_names()) == {*catalog_metadata.tables, "alembic_version"}
    context = MigrationContext.configure(connection, opts={
        "compare_type": True, "compare_server_default": True,
    })
    assert compare_metadata(context, catalog_metadata) == []
    for name in ("ProductImages", "Tags", "ProductTags"):
        assert inspector.get_check_constraints(name) == []
        assert all(not column["nullable"] for column in inspector.get_columns(name))
    # pg_constraint registra NO ACTION como 'a', incluso al omitir la cláusula.
    actions = connection.exec_driver_sql(
        "SELECT conname, confdeltype, confupdtype FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace AND contype = 'f' "
        "AND conrelid IN ('public.\"ProductImages\"'::regclass, "
        "'public.\"ProductTags\"'::regclass)"
    ).all()
    assert len(actions) == 3
    assert all(delete_action == update_action == "a" for _, delete_action, update_action in actions)


def test_generated_ids_defaults_and_variant_relationships(enrichment_catalog):
    data = enrichment_catalog
    session = data.session
    assert data.image.product_image_id > 0 and data.tag.tag_id > 0
    assert data.image.created_at == data.tag.created_at == session.scalar(select(func.now()))
    assert data.image.created_at.utcoffset() is not None
    assert data.image.variant is data.assignment.variant is data.variant
    assert data.assignment.tag is data.tag
    assert data.tag.name == "picante"
    inspector = inspect(session.connection())
    for table, key in (("ProductImages", "product_image_id"), ("Tags", "tag_id")):
        column = next(col for col in inspector.get_columns(table) if col["name"] == key)
        assert column["autoincrement"] is True
        assert column["default"].startswith("nextval(")


def test_same_tag_can_be_assigned_to_different_variants(enrichment_catalog):
    data = enrichment_catalog
    data.session.add(ProductTag(variant=data.other, tag=data.tag))
    data.session.flush()
    assert data.session.scalar(select(func.count()).select_from(ProductTag)) == 2


@pytest.mark.parametrize("entity", ["tag", "assignment"])
def test_duplicate_names_and_assignments_are_rejected(enrichment_catalog, entity):
    data = enrichment_catalog
    with pytest.raises(IntegrityError) as error:
        with data.session.begin_nested():
            if entity == "tag":
                data.session.add(Tag(name=" PiCaNtE "))
                data.session.flush()
            else:
                data.session.execute(insert(ProductTag).values(
                    product_variant_id=data.variant.product_variant_id, tag_id=data.tag.tag_id,
                ))
    assert error.value.orig.pgcode == "23505"
    assert data.session.scalar(select(func.count()).select_from(Tag)) == 1
    assert data.session.scalar(select(func.count()).select_from(ProductTag)) == 1


def test_postgresql_enforces_tag_name_length_even_for_core_writes(enrichment_catalog):
    data = enrichment_catalog
    with pytest.raises(DataError) as error:
        with data.session.begin_nested():
            data.session.execute(insert(Tag).values(name="x" * 151))
    assert error.value.orig.pgcode == "22001"


@pytest.mark.parametrize("model,values", [
    (ProductImage, {"product_variant_id": -1, "image_url": "/media/no.webp", "display_order": 0}),
    (ProductTag, {"product_variant_id": -1}),
    (ProductTag, {"tag_id": -1}),
])
def test_foreign_keys_reject_orphans(enrichment_catalog, model, values):
    data = enrichment_catalog
    if model is ProductTag:
        values = {"product_variant_id": data.variant.product_variant_id,
                  "tag_id": data.tag.tag_id, **values}
    with pytest.raises(IntegrityError) as error:
        with data.session.begin_nested():
            data.session.execute(insert(model).values(**values))
    assert error.value.orig.pgcode == "23503"


@pytest.mark.parametrize("model,values", [
    (ProductImage, {"product_variant_id": None, "image_url": "/media/test.webp", "display_order": 0}),
    (ProductImage, {"image_url": None, "display_order": 0}),
    (ProductImage, {"image_url": "/media/test.webp", "display_order": None}),
    (ProductImage, {"image_url": "/media/test.webp", "display_order": 0, "created_at": None}),
    (Tag, {"name": None}),
    (Tag, {"name": "otra", "created_at": None}),
    (ProductTag, {"product_variant_id": None}),
    (ProductTag, {"tag_id": None}),
])
def test_required_columns_reject_nulls(enrichment_catalog, model, values):
    data = enrichment_catalog
    if model in (ProductImage, ProductTag):
        values = {"product_variant_id": data.variant.product_variant_id, **values}
    if model is ProductTag:
        values = {"tag_id": data.tag.tag_id, **values}
    with pytest.raises(IntegrityError) as error:
        with data.session.begin_nested():
            data.session.execute(insert(model).values(**values))
    assert error.value.orig.pgcode == "23502"


@pytest.mark.parametrize("parent", ["variant", "tag"])
def test_parent_deletion_does_not_cascade(enrichment_catalog, parent):
    data = enrichment_catalog
    target = data.variant if parent == "variant" else data.tag
    primary_key = next(iter(type(target).__table__.primary_key.columns))
    identifier = data.variant.product_variant_id if parent == "variant" else data.tag.tag_id
    with pytest.raises(IntegrityError) as error:
        with data.session.begin_nested():
            data.session.execute(delete(type(target)).where(primary_key == identifier))
    assert error.value.orig.pgcode == "23503"
    assert data.session.get(ProductImage, data.image.product_image_id) is not None
    assert data.session.get(ProductTag, (data.variant.product_variant_id, data.tag.tag_id)) is not None


@pytest.mark.parametrize("loaded", [False, True])
def test_orm_tag_deletion_also_respects_no_action(enrichment_catalog, loaded):
    data = enrichment_catalog
    data.session.expire(data.tag, ["variant_assignments"])
    if loaded:
        assert len(data.tag.variant_assignments) == 1
    with pytest.raises(IntegrityError) as error:
        with data.session.begin_nested():
            data.session.delete(data.tag)
            data.session.flush()
    assert error.value.orig.pgcode == "23503"


def test_image_order_can_tie_without_an_unapproved_unique_constraint(enrichment_catalog):
    data = enrichment_catalog
    second = ProductImage(variant=data.variant, image_url="/media/second.webp", display_order=2)
    data.session.add(second)
    data.session.flush()
    images = data.session.scalars(select(ProductImage).order_by(
        ProductImage.display_order, ProductImage.product_image_id,
    )).all()
    assert [image.product_image_id for image in images] == [data.image.product_image_id, second.product_image_id]


@pytest.mark.parametrize("retire_product", [False, True])
def test_hu008_keeps_local_enrichment_when_syncing_or_retiring(
    enrichment_catalog, sync_db, commercial_snapshot, retire_product,
):
    data = enrichment_catalog
    data.variant.product.description = "Descripción local conservada"
    data.session.commit()  # Libera el savepoint; la transacción externa sigue aislada.

    def local_rows():
        return {model.__tablename__: sync_db.connection.execute(
            select(model.__table__).order_by(*model.__table__.primary_key.columns)
        ).all() for model in (ProductImage, Tag, ProductTag)}

    before = local_rows()
    snapshot = deepcopy(commercial_snapshot)
    if retire_product:
        snapshot.items[0].clear()
        snapshot.items[0].update(id="burger", deleted_at="2026-11-02T18:00:00Z")
    sync_db.service.persist_snapshot(snapshot)
    assert local_rows() == before
    product = sync_db.connection.execute(select(Product.__table__).where(
        Product.product_id == data.variant.product_id,
    )).mappings().one()
    assert product["description"] == "Descripción local conservada"
    assert product["is_active"] is (not retire_product)


@pytest.fixture(scope="module")
def initial_catalog_database(sync_test_database, record_testsuite_property):
    """Otra base aislada permite probar el salto incremental con datos simulados."""
    name = "mitbol_test_hu008_" + uuid4().hex[:12]
    url = sync_test_database.url.set(database=name)
    maintenance = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with maintenance.connect() as connection:
            assert connection.scalar(select(func.current_database())) == "postgres"
            quoted = connection.dialect.identifier_preparer.quote(name)
            connection.exec_driver_sql(f"CREATE DATABASE {quoted} TEMPLATE template0 ENCODING 'UTF8'")
    finally:
        maintenance.dispose()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = url.render_as_string(hide_password=False)
    command.upgrade(config, INITIAL_REVISION)
    engine = create_engine(url, connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000"})
    try:
        with engine.connect() as connection:
            assert connection.scalar(select(func.current_database())) == name
        yield SimpleNamespace(engine=engine, config=config)
    finally:
        record_testsuite_property("incremental_migration_test_database", name)
        engine.dispose()  # Se conserva la base de pruebas; nunca se ejecuta DROP DATABASE.


def _commercial_state(engine):
    with engine.connect() as connection:
        inspector = inspect(connection)
        metadata = MetaData()
        metadata.reflect(connection, only=sorted(CATALOG_TABLES))
        return {
            name: {
                "columns": [(col["name"], col["type"].compile(dialect=connection.dialect),
                             col["nullable"], col["default"])
                            for col in inspector.get_columns(name)],
                "pk": inspector.get_pk_constraint(name),
                "fk": inspector.get_foreign_keys(name),
                "unique": inspector.get_unique_constraints(name),
                "rows": connection.execute(select(metadata.tables[name]).order_by(
                    *metadata.tables[name].primary_key.columns,
                )).all(),
            } for name in sorted(CATALOG_TABLES)
        }


def test_incremental_upgrade_preserves_existing_commercial_schema_and_data(
    initial_catalog_database, commercial_snapshot, monkeypatch,
):
    database = initial_catalog_database
    # Reproduce datos HU-008 con sus tablas históricas reflejadas. El servicio
    # actual requiere HU-007 y no debe ejecutarse contra un esquema anterior.
    plan = transform_catalog(commercial_snapshot)
    with database.engine.begin() as connection:
        metadata = MetaData()
        metadata.reflect(connection)
        products, modifiers, variants = {}, {}, {}
        for product in plan.products:
            table = metadata.tables["Products"]
            products[product.loyverse_id] = connection.scalar(insert(table).values(
                loyverse_id=product.loyverse_id, name=product.name, synced_at=plan.synced_at,
            ).returning(table.c.product_id))
        for modifier in plan.modifiers:
            table = metadata.tables["ProductModifiers"]
            modifiers[modifier.loyverse_id] = connection.scalar(insert(table).values(
                loyverse_id=modifier.loyverse_id, name=modifier.name,
                display_order=modifier.display_order, synced_at=plan.synced_at,
            ).returning(table.c.product_modifier_id))
        for variant in plan.variants:
            table = metadata.tables["ProductVariants"]
            variants[variant.loyverse_id] = connection.scalar(insert(table).values(
                loyverse_id=variant.loyverse_id, name=variant.name, price=variant.price,
                product_id=products[variant.product_loyverse_id], synced_at=plan.synced_at,
                is_available=variant.is_commercially_available,
            ).returning(table.c.product_variant_id))
        for complement in plan.complements:
            connection.execute(insert(metadata.tables["ProductComplements"]).values(
                loyverse_id=complement.loyverse_id, name=complement.name, price=complement.price,
                product_modifier_id=modifiers[complement.modifier_loyverse_id],
                display_order=complement.display_order, synced_at=plan.synced_at,
            ))
        for variant_id, modifier_id in plan.assignments:
            connection.execute(insert(metadata.tables["ProductModifierAssignments"]).values(
                product_variant_id=variants[variant_id], product_modifier_id=modifiers[modifier_id],
            ))
    before = _commercial_state(database.engine)
    assert [len(before[name]["rows"]) for name in (
        "Products", "ProductVariants", "ProductModifiers", "ProductComplements",
        "ProductModifierAssignments",
    )] == [3, 5, 2, 5, 4]
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Alembic intentó abrir otra conexión en lugar de usar la verificada")

    with database.engine.begin() as connection:
        database.config.attributes["connection"] = connection
        try:
            with monkeypatch.context() as patch:
                patch.setattr("sqlalchemy.create_engine", unexpected_connection)
                command.upgrade(database.config, ENRICHMENT_REVISION)
            assert connection.in_transaction()
        finally:
            database.config.attributes.pop("connection", None)
    assert _commercial_state(database.engine) == before
    with database.engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == (ENRICHMENT_REVISION,)
        assert set(inspect(connection).get_table_names()) == {*catalog_metadata.tables, "alembic_version"}
        for model in (ProductImage, Tag, ProductTag):
            assert connection.scalar(select(func.count()).select_from(model)) == 0

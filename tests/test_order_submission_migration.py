"""La migración consolidada se prueba sólo en PostgreSQL desechable."""

from io import StringIO

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select

from app.models import AuthBase, Base, OrderBase, RestaurantOrderSettings
from app.models.sync_run import SyncBase
from test_auth_persistence import config_for


PREVIOUS = "20261115_0008"
REVISION = "20261119_0009"
TABLES = {"Orders", "OrderDetails", "OrderDetailComplements", "RestaurantOrderSettings"}


def test_consolidated_revision_is_additive_and_offline_sql_has_no_grants():
    output = StringIO()
    config = config_for(output=output)
    command.upgrade(config, f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert ScriptDirectory.from_config(config).get_heads() == ["20261119_0010"]
    assert sql.count("CREATE TABLE") == 4
    assert all(f'CREATE TABLE "{name}"' in sql for name in TABLES)
    assert not any(operation in sql for operation in ("ALTER TABLE", "DROP TABLE", "CREATE ROLE", "GRANT ", "TRUNCATE"))
    assert 'INSERT INTO "RestaurantOrderSettings"' in sql


def test_upgrade_matches_models_preserves_catalog_and_downgrade_roundtrips(empty_postgresql_connection):
    db = empty_postgresql_connection
    config = config_for(db)
    command.upgrade(config, PREVIOUS)
    before = set(inspect(db).get_table_names())
    assert not before & TABLES
    product = Base.metadata.tables["Products"]
    marker = db.scalar(product.insert().values(loyverse_id="hu021-marker", name="Conservar catálogo",
                                               synced_at="2026-11-19T12:00:00+00:00").returning(product.c.product_id))
    command.upgrade(config, REVISION)
    assert set(inspect(db).get_table_names()) == before | TABLES
    assert MigrationContext.configure(db).get_current_heads() == (REVISION,)
    # El modelo vigente incluye el delta de seguimiento; 0009 conserva su contrato histórico.
    command.upgrade(config, "20261119_0010")
    assert compare_metadata(MigrationContext.configure(db),
                            [Base.metadata, AuthBase.metadata, SyncBase.metadata, OrderBase.metadata]) == []
    assert db.execute(select(RestaurantOrderSettings.__table__)).one() == (1, False)
    relations = inspect(db)
    assert {fk["referred_table"] for fk in relations.get_foreign_keys("Orders")} == {"PersonalAccounts"}
    assert {fk["referred_table"] for fk in relations.get_foreign_keys("OrderDetails")} == {"Orders", "Products", "ProductVariants"}
    assert {fk["referred_table"] for fk in relations.get_foreign_keys("OrderDetailComplements")} == {
        "OrderDetails", "ProductComplements", "ProductModifiers"}
    command.downgrade(config, PREVIOUS)
    assert set(inspect(db).get_table_names()) == before
    assert db.scalar(select(product.c.name).where(product.c.product_id == marker)) == "Conservar catálogo"
    command.upgrade(config, REVISION)
    assert db.execute(select(RestaurantOrderSettings.__table__)).one() == (1, False)

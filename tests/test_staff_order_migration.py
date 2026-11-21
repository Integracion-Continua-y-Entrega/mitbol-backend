"""0013 -> 0014 y reversión sólo en PostgreSQL desechable."""

from io import StringIO
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db.schema_compatibility import validate_bridge_schema
from app.models import (AuthBase, Base, CommercialDiscountBase, CouponBase, Order, OrderBase,
                        OrderStateBase, OrderStatusHistory, PasswordRecoveryBase, RestaurantOrderSettings)
from app.models.sync_run import SyncBase
from test_auth_persistence import config_for


PREVIOUS = "20261120_0013"
REVISION = "20261121_0014"


def test_history_revision_is_additive_offline_and_has_no_external_changes():
    output = StringIO()
    config = config_for(output=output)
    command.upgrade(config,f"{PREVIOUS}:{REVISION}",sql=True)
    sql = output.getvalue()
    assert ScriptDirectory.from_config(config).get_heads() == [REVISION]
    assert ScriptDirectory.from_config(config).get_revision(REVISION).down_revision == PREVIOUS
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "OrderStatusHistories"' in sql
    assert not any(value in sql for value in ("ALTER TABLE","CREATE ROLE","GRANT ",'UPDATE "Orders"','INSERT INTO "Orders"'))


def test_history_migration_preserves_orders_matches_metadata_and_roundtrips(empty_postgresql_connection):
    db = empty_postgresql_connection
    config = config_for(db)
    command.upgrade(config,PREVIOUS)
    tables = set(inspect(db).get_table_names())
    identifier = uuid4()
    db.execute(Order.__table__.insert().values(order_id=identifier,idempotency_key=uuid4(),request_hash="0"*64,
        modality="dine_in",requester_name="Histórico",subtotal=10,discount_total=0,total=10))
    command.upgrade(config,REVISION)
    assert set(inspect(db).get_table_names()) == tables | {"OrderStatusHistories"}
    assert validate_bridge_schema(db) == REVISION
    assert not compare_metadata(MigrationContext.configure(db),[Base.metadata,AuthBase.metadata,SyncBase.metadata,
        OrderBase.metadata,PasswordRecoveryBase.metadata,CouponBase.metadata,CommercialDiscountBase.metadata,OrderStateBase.metadata])
    assert not db.scalar(select(RestaurantOrderSettings.accepting_orders))
    assert not db.execute(select(OrderStatusHistory)).all()  # No inventa transiciones históricas.
    db.execute(OrderStatusHistory.__table__.insert().values(order_id=identifier,status="Aceptado",previous_status="Solicitud recibida"))
    with pytest.raises(IntegrityError),db.begin_nested():
        db.execute(OrderStatusHistory.__table__.insert().values(order_id=uuid4(),status="Rechazado"))
    with pytest.raises(IntegrityError),db.begin_nested():
        db.execute(OrderStatusHistory.__table__.insert().values(order_id=identifier,status="Rechazado",reason=" "))
    command.downgrade(config,PREVIOUS)
    assert set(inspect(db).get_table_names()) == tables
    assert db.scalar(select(Order.total).where(Order.order_id == identifier)) == 10
    assert validate_bridge_schema(db) == PREVIOUS
    command.upgrade(config,REVISION)
    assert not db.execute(select(OrderStatusHistory)).all()

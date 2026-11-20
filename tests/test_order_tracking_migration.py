"""Delta mínimo de HU-022 sobre un esquema HU-021 desechable."""

from io import StringIO
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, select

from app.models import AuthBase, Base, Order, OrderBase
from app.models.sync_run import SyncBase
from test_auth_persistence import config_for


PREVIOUS = "20261119_0009"
REVISION = "20261119_0010"


def test_tracking_delta_adds_only_private_access_without_grants_or_data_backfill():
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert 'ALTER TABLE "Orders" ADD COLUMN guest_tracking_token VARCHAR(43)' in sql
    assert not any(operation in sql for operation in ("CREATE TABLE", "CREATE ROLE", "GRANT ", 'UPDATE "Orders"', "DROP TABLE"))


def test_tracking_upgrade_and_downgrade_preserve_existing_orders_and_match_models(empty_postgresql_connection):
    db = empty_postgresql_connection
    config = config_for(db)
    command.upgrade(config, PREVIOUS)
    before = set(inspect(db).get_table_names())
    identifier = uuid4()
    db.execute(Order.__table__.insert().values(order_id=identifier, idempotency_key=uuid4(), request_hash="a" * 64,
        modality="dine_in", requester_name="Invitado anterior", subtotal=0, discount_total=0, total=0))
    command.upgrade(config, REVISION)
    assert set(inspect(db).get_table_names()) == before
    assert db.scalar(select(Order.guest_tracking_token).where(Order.order_id == identifier)) is None
    assert compare_metadata(MigrationContext.configure(db),
                            [Base.metadata, AuthBase.metadata, SyncBase.metadata, OrderBase.metadata]) == []
    command.downgrade(config, PREVIOUS)
    assert "guest_tracking_token" not in {column["name"] for column in inspect(db).get_columns("Orders")}
    assert db.scalar(select(Order.requester_name).where(Order.order_id == identifier)) == "Invitado anterior"
    command.upgrade(config, REVISION)
    assert db.scalar(select(Order.guest_tracking_token).where(Order.order_id == identifier)) is None

"""HU-022: acceso privado opcional para pedidos de invitados; sin backfill ni grants."""

from alembic import op
import sqlalchemy as sa

revision = "20261119_0010"
down_revision = "20261119_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("Orders", sa.Column("guest_tracking_token", sa.String(43), nullable=True))
    op.create_unique_constraint("uq_Orders_guest_tracking_token", "Orders", ["guest_tracking_token"])
    op.create_check_constraint("ck_Orders_guest_tracking_token", "Orders",
        "guest_tracking_token IS NULL OR (account_id IS NULL AND guest_tracking_token ~ '^[A-Za-z0-9_-]{43}$')")


def downgrade():
    op.drop_constraint("ck_Orders_guest_tracking_token", "Orders", type_="check")
    op.drop_constraint("uq_Orders_guest_tracking_token", "Orders", type_="unique")
    op.drop_column("Orders", "guest_tracking_token")

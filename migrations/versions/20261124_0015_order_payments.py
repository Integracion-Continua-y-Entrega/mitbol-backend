"""HU-031: persistencia de verificación manual; no infiere pagos ni otorga permisos."""

from alembic import op
import sqlalchemy as sa


revision = "20261124_0015"
down_revision = "20261121_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "OrderPayments",
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.Column("verified_by_account_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("payment_id", name="pk_OrderPayments"),
        sa.UniqueConstraint("order_id", name="uq_OrderPayments_order_id"),
        sa.ForeignKeyConstraint(["order_id"], ["Orders.order_id"],
                                name="fk_OrderPayments_order_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["verified_by_account_id"], ["PersonalAccounts.account_id"],
                                name="fk_OrderPayments_verified_by_account_id", ondelete="RESTRICT"),
        sa.CheckConstraint("amount > 0 AND amount < 'Infinity'::numeric", name="ck_OrderPayments_amount"),
        sa.CheckConstraint("method IN ('cash', 'card', 'transfer', 'other')", name="ck_OrderPayments_method"),
    )


def downgrade():
    op.drop_table("OrderPayments")

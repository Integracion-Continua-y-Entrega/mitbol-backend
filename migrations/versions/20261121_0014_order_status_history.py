"""Historial mínimo de solicitudes; no cambia pedidos ni permisos existentes."""

from alembic import op
import sqlalchemy as sa


revision = "20261121_0014"
down_revision = "20261120_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "OrderStatusHistories",
        sa.Column("status_history_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("previous_status", sa.String(50), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("actor_account_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.PrimaryKeyConstraint("status_history_id", name="pk_OrderStatusHistories"),
        sa.ForeignKeyConstraint(["order_id"],["Orders.order_id"], name="fk_OrderStatusHistories_order_id",ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_account_id"],["PersonalAccounts.account_id"],
                                name="fk_OrderStatusHistories_actor_account_id",ondelete="RESTRICT"),
        sa.CheckConstraint("reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500",name="ck_OrderStatusHistories_reason"),
    )
    op.create_index("ix_OrderStatusHistories_order_created","OrderStatusHistories",["order_id","created_at"])


def downgrade():
    op.drop_table("OrderStatusHistories")

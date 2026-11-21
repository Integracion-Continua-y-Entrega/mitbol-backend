"""HU-019: asociaciones explícitas y descuentos históricos; sin promociones reales ni grants."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261120_0013"
down_revision = "20261119_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "CommercialDiscountAssignments",
        sa.Column("discount_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("conditions", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.PrimaryKeyConstraint("discount_id", "product_id", name="pk_CommercialDiscountAssignments"),
        sa.ForeignKeyConstraint(["discount_id"], ["Discounts.discount_id"],
                                name="fk_CommercialDiscountAssignments_discount_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["Products.product_id"],
                                name="fk_CommercialDiscountAssignments_product_id", ondelete="CASCADE"),
        sa.CheckConstraint("conditions IS NULL OR jsonb_typeof(conditions) = 'object'",
                           name="ck_CommercialDiscountAssignments_conditions"),
    )
    op.create_table(
        "OrderDiscountSnapshots",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("calculation", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("order_id", name="pk_OrderDiscountSnapshots"),
        sa.ForeignKeyConstraint(["order_id"], ["Orders.order_id"],
                                name="fk_OrderDiscountSnapshots_order_id", ondelete="CASCADE"),
        sa.CheckConstraint("jsonb_typeof(calculation) = 'object'", name="ck_OrderDiscountSnapshots_calculation"),
    )


def downgrade():
    op.drop_table("OrderDiscountSnapshots")
    op.drop_table("CommercialDiscountAssignments")

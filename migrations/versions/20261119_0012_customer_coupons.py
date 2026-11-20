"""HU-020: cupones asociados a cuentas, sin datos comerciales inventados."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261119_0012"
down_revision = "20261119_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "CustomerCoupons",
        sa.Column("customer_coupon_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("discount_id", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default="available", nullable=False),
        sa.Column("conditions", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("customer_coupon_id", name="pk_CustomerCoupons"),
        sa.ForeignKeyConstraint(["account_id"], ["PersonalAccounts.account_id"],
                                name="fk_CustomerCoupons_account_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["discount_id"], ["Discounts.discount_id"],
                                name="fk_CustomerCoupons_discount_id", ondelete="RESTRICT"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_CustomerCoupons_name"),
        sa.CheckConstraint("status IN ('available', 'reserved', 'redeemed', 'expired')", name="ck_CustomerCoupons_status"),
        sa.CheckConstraint("starts_at < expires_at", name="ck_CustomerCoupons_validity"),
        sa.CheckConstraint("conditions IS NULL OR jsonb_typeof(conditions) = 'object'", name="ck_CustomerCoupons_conditions"),
    )
    op.create_index("ix_CustomerCoupons_account_coupon", "CustomerCoupons", ["account_id", "customer_coupon_id"])


def downgrade():
    op.drop_table("CustomerCoupons")

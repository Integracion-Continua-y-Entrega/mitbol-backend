"""HU-045: evidencia de concesión única; no emite cupones ni concede permisos."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261125_0019"
down_revision = "20261125_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "LoyaltyRewards",
        sa.Column("reward_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("customer_coupon_id", sa.BigInteger(), nullable=False),
        sa.Column("goal_version", sa.Integer(), nullable=False),
        sa.Column("required_visits", sa.Integer(), nullable=False),
        sa.Column("total_visits", sa.BigInteger(), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("goal_snapshot", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("reward_id", name="pk_LoyaltyRewards"),
        sa.UniqueConstraint("account_id", "goal_id", name="uq_LoyaltyRewards_account_goal"),
        sa.UniqueConstraint("customer_coupon_id", name="uq_LoyaltyRewards_coupon"),
        sa.ForeignKeyConstraint(["account_id"], ["PersonalAccounts.account_id"], name="fk_LoyaltyRewards_account_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["goal_id"], ["LoyaltyGoals.goal_id"], name="fk_LoyaltyRewards_goal_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["visit_id"], ["LoyaltyVisits.visit_id"], name="fk_LoyaltyRewards_visit_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["customer_coupon_id"], ["CustomerCoupons.customer_coupon_id"], name="fk_LoyaltyRewards_customer_coupon_id", ondelete="RESTRICT"),
        sa.CheckConstraint("goal_version > 0", name="ck_LoyaltyRewards_version"),
        sa.CheckConstraint("required_visits > 0 AND total_visits >= required_visits", name="ck_LoyaltyRewards_visits"),
        sa.CheckConstraint("isfinite(awarded_at)", name="ck_LoyaltyRewards_awarded_at"),
        sa.CheckConstraint("jsonb_typeof(goal_snapshot) = 'object'", name="ck_LoyaltyRewards_snapshot"),
    )


def downgrade():
    op.drop_table("LoyaltyRewards")

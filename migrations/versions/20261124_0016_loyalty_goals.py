"""HU-046: configuración de metas; no crea recompensas, datos de ejemplo ni permisos."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261124_0016"
down_revision = "20261124_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "LoyaltyGoals",
        sa.Column("goal_id", sa.Uuid(), nullable=False),
        sa.Column("required_visits", sa.Integer(), nullable=False),
        sa.Column("level_name", sa.String(50), nullable=False),
        sa.Column("reward_name", sa.String(100), nullable=False),
        sa.Column("reward_description", sa.Text(), nullable=True),
        sa.Column("discount_id", sa.Integer(), nullable=False),
        sa.Column("valid_days", sa.SmallInteger(), nullable=False),
        sa.Column("conditions", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("clock_timestamp()")),
        sa.Column("updated_by_account_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("goal_id", name="pk_LoyaltyGoals"),
        sa.UniqueConstraint("required_visits", name="uq_LoyaltyGoals_required_visits"),
        sa.ForeignKeyConstraint(["discount_id"], ["Discounts.discount_id"], name="fk_LoyaltyGoals_discount_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_account_id"], ["PersonalAccounts.account_id"],
                                name="fk_LoyaltyGoals_updated_by_account_id", ondelete="RESTRICT"),
        sa.CheckConstraint("required_visits > 0", name="ck_LoyaltyGoals_visits"),
        sa.CheckConstraint("valid_days > 0", name="ck_LoyaltyGoals_valid_days"),
        sa.CheckConstraint("version > 0", name="ck_LoyaltyGoals_version"),
        sa.CheckConstraint("length(btrim(level_name)) > 0", name="ck_LoyaltyGoals_level_name"),
        sa.CheckConstraint("length(btrim(reward_name)) > 0", name="ck_LoyaltyGoals_reward_name"),
        sa.CheckConstraint("jsonb_typeof(conditions) = 'object'", name="ck_LoyaltyGoals_conditions"),
    )


def downgrade():
    op.drop_table("LoyaltyGoals")

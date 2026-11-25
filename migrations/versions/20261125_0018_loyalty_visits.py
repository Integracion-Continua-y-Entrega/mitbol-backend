"""HU-043: registro durable y único por cuenta y día local; sin visitas automáticas ni grants."""

from alembic import op
import sqlalchemy as sa


revision = "20261125_0018"
down_revision = "20261124_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "LoyaltyVisits",
        sa.Column("visit_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("visited_on", sa.Date(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restaurant_timezone", sa.String(64), nullable=False),
        sa.Column("registered_by_account_id", sa.Integer(), nullable=False),
        sa.Column("identification_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("visit_id", name="pk_LoyaltyVisits"),
        sa.UniqueConstraint("account_id", "visited_on", name="uq_LoyaltyVisits_account_day"),
        sa.UniqueConstraint("identification_hash", name="uq_LoyaltyVisits_identification_hash"),
        sa.ForeignKeyConstraint(["account_id"], ["PersonalAccounts.account_id"], name="fk_LoyaltyVisits_account_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registered_by_account_id"], ["PersonalAccounts.account_id"],
                                name="fk_LoyaltyVisits_registered_by_account_id", ondelete="RESTRICT"),
        sa.CheckConstraint("identification_hash ~ '^[0-9a-f]{64}$'", name="ck_LoyaltyVisits_identification_hash"),
        sa.CheckConstraint("length(btrim(restaurant_timezone)) > 0", name="ck_LoyaltyVisits_timezone"),
        sa.CheckConstraint("isfinite(registered_at)", name="ck_LoyaltyVisits_registered_at"),
        sa.CheckConstraint("visited_on = CAST(timezone(restaurant_timezone, registered_at) AS DATE)", name="ck_LoyaltyVisits_local_date"),
    )


def downgrade():
    op.drop_table("LoyaltyVisits")

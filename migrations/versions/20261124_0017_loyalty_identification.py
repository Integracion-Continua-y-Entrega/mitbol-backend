"""HU-042: credencial temporal almacenada como hash, sin emisión, visitas ni grants."""

from alembic import op
import sqlalchemy as sa


revision = "20261124_0017"
down_revision = "20261124_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "LoyaltyIdentifications",
        sa.Column("account_id", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_by_account_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("account_id", name="pk_LoyaltyIdentifications"),
        sa.UniqueConstraint("token_hash", name="uq_LoyaltyIdentifications_token_hash"),
        sa.ForeignKeyConstraint(["account_id"], ["PersonalAccounts.account_id"], name="fk_LoyaltyIdentifications_account_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["AccountSessions.session_id"], name="fk_LoyaltyIdentifications_session_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["consumed_by_account_id"], ["PersonalAccounts.account_id"],
                                name="fk_LoyaltyIdentifications_consumed_by_account_id", ondelete="RESTRICT"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_LoyaltyIdentifications_hash"),
        sa.CheckConstraint("auth_version >= 0", name="ck_LoyaltyIdentifications_auth_version"),
        sa.CheckConstraint("created_at < expires_at", name="ck_LoyaltyIdentifications_expiration"),
        sa.CheckConstraint("(consumed_at IS NULL) = (consumed_by_account_id IS NULL)", name="ck_LoyaltyIdentifications_consumption"),
        sa.CheckConstraint("consumed_at IS NULL OR (created_at <= consumed_at AND consumed_at < expires_at)",
                           name="ck_LoyaltyIdentifications_consumed_time"),
    )


def downgrade():
    op.drop_table("LoyaltyIdentifications")

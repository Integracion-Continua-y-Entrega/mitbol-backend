"""HU-013: recuperación temporal de contraseñas, sin secretos en claro."""

from alembic import op
import sqlalchemy as sa


revision = "20261119_0011"
down_revision = "20261119_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "PasswordResetTokens",
        sa.Column("account_id", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("account_id", name="pk_PasswordResetTokens"),
        sa.ForeignKeyConstraint(["account_id"], ["PersonalAccounts.account_id"],
                                name="fk_PasswordResetTokens_account_id", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_PasswordResetTokens_token_hash"),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_PasswordResetTokens_hash"),
        sa.CheckConstraint("auth_version >= 0", name="ck_PasswordResetTokens_auth_version"),
        sa.CheckConstraint("created_at < expires_at", name="ck_PasswordResetTokens_expiration"),
    )


def downgrade():
    op.drop_table("PasswordResetTokens")

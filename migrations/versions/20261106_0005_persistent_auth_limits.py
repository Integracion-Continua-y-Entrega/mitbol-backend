"""Limitación compartida, sin credenciales, roles ni grants.

Revision ID: 20261106_0005
Revises: 20261105_0004
"""
from alembic import op
import sqlalchemy as sa

revision = "20261106_0005"
down_revision = "20261105_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("AuthRateLimitPolicy",
        sa.Column("policy_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("policy_id", name="pk_AuthRateLimitPolicy"),
        sa.CheckConstraint("policy_id = 1", name="ck_AuthRateLimitPolicy_singleton"),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_AuthRateLimitPolicy_fingerprint"))
    op.create_table("AuthRateLimits",
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key_hash", name="pk_AuthRateLimits"),
        sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_AuthRateLimits_key"),
        sa.CheckConstraint("attempts > 0", name="ck_AuthRateLimits_attempts"),
        sa.CheckConstraint("expires_at > started_at", name="ck_AuthRateLimits_expiry"))
    op.create_index("ix_AuthRateLimits_expires_at", "AuthRateLimits", ["expires_at"])


def downgrade():
    op.drop_index("ix_AuthRateLimits_expires_at", table_name="AuthRateLimits")
    op.drop_table("AuthRateLimits")
    op.drop_table("AuthRateLimitPolicy")

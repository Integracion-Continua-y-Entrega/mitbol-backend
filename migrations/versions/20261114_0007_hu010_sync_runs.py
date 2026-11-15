"""HU-010: cola durable e historial; no altera catálogo, identidad ni grants."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261114_0007"
down_revision = "20261111_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "SyncRuns",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_key", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.Column("store_id", sa.String(100), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("attempt_token", sa.Uuid(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("run_id", name="pk_SyncRuns"),
        sa.CheckConstraint("catalog_key = 1", name="ck_SyncRuns_singleton_catalog"),
        sa.CheckConstraint("source IN ('manual', 'scheduled', 'webhook')", name="ck_SyncRuns_source"),
        sa.CheckConstraint("status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed')", name="ck_SyncRuns_status"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 3", name="ck_SyncRuns_attempts"),
        sa.CheckConstraint("(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)", name="ck_SyncRuns_finished"),
        schema="public",
    )
    op.create_index("uq_sync_runs_active", "SyncRuns", ["catalog_key"], unique=True, schema="public",
                    postgresql_where=sa.text("status IN ('queued', 'running', 'retry_wait')"))
    op.create_index("ix_sync_runs_created", "SyncRuns", ["created_at", "run_id"], schema="public")


def downgrade():
    # Solo reversión explícita de HU-010; elimina su cola/historial. Nunca automática.
    op.drop_table("SyncRuns", schema="public")

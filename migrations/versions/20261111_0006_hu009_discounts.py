"""HU-009: añade Discounts sin modificar datos o tablas anteriores."""

from alembic import op
import sqlalchemy as sa


revision = "20261111_0006"
down_revision = "20261106_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "Discounts",
        sa.Column("discount_id", sa.Integer(), nullable=False),
        sa.Column("loyverse_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("discount_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("discount_id", name="pk_Discounts"),
        sa.UniqueConstraint("loyverse_id", name="uq_Discounts_loyverse_id"),
    )


def downgrade():
    # Reversión destructiva solo de la tabla incorporada por esta revisión.
    op.drop_table("Discounts")

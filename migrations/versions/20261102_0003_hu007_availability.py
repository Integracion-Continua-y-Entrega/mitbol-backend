"""Separa disponibilidad comercial y local sin inferir datos históricos.

Revision ID: 20261102_0003
Revises: 20261102_0002

Las filas existentes conservan is_available. El nuevo false bloquea la venta
hasta verificar el estado comercial mediante una sincronización autorizada.
Aplicar en mitbol_dev requiere autorización expresa.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261102_0003"
down_revision = "20261102_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ProductVariants", sa.Column(
        "is_commercially_available", sa.Boolean(), server_default=sa.false(), nullable=False,
    ))


def downgrade() -> None:
    # Elimina la separación comercial; requiere restaurar código compatible.
    op.drop_column("ProductVariants", "is_commercially_available")

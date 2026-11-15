"""HU-008/HU-002: categorías comerciales y relación opcional de producto.

Upgrade aditivo: no modifica valores de columnas existentes ni aplica grants.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261115_0008"
down_revision = "20261114_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "Categories",
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("loyverse_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("category_id", name="pk_Categories"),
        sa.UniqueConstraint("loyverse_id", name="uq_Categories_loyverse_id"),
    )
    op.add_column("Products", sa.Column("category_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_Products_category_id", "Products", "Categories",
                          ["category_id"], ["category_id"], ondelete="RESTRICT")
    op.create_index("ix_Products_category_id", "Products", ["category_id"])


def downgrade():
    # Destructivo para la nueva clasificación: nunca usar como rollback de despliegue.
    op.drop_index("ix_Products_category_id", table_name="Products")
    op.drop_constraint("fk_Products_category_id", "Products", type_="foreignkey")
    op.drop_column("Products", "category_id")
    op.drop_table("Categories")

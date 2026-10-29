"""Catálogo comercial de HU-008 según el diagrama aprobado.

Revision ID: 20261029_0001
Revises: None

Los DEFAULT booleanos fueron aceptados para este borrador porque la imagen
recorta parte de sus valores. Aplicar esta revisión requiere autorización.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261029_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "Products",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("loyverse_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_new", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("product_id", name="pk_Products"),
        sa.UniqueConstraint("loyverse_id", name="uq_Products_loyverse_id"),
    )
    op.create_table(
        "ProductModifiers",
        sa.Column("product_modifier_id", sa.Integer(), nullable=False),
        sa.Column("loyverse_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("product_modifier_id", name="pk_ProductModifiers"),
        sa.UniqueConstraint("loyverse_id", name="uq_ProductModifiers_loyverse_id"),
    )
    op.create_table(
        "ProductVariants",
        sa.Column("product_variant_id", sa.Integer(), nullable=False),
        sa.Column("loyverse_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("product_variant_id", name="pk_ProductVariants"),
        sa.UniqueConstraint("loyverse_id", name="uq_ProductVariants_loyverse_id"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["Products.product_id"],
            name="fk_ProductVariants_product_id",
        ),
    )
    op.create_table(
        "ProductComplements",
        sa.Column("product_complement_id", sa.Integer(), nullable=False),
        sa.Column("product_modifier_id", sa.Integer(), nullable=False),
        sa.Column("loyverse_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("product_complement_id", name="pk_ProductComplements"),
        sa.UniqueConstraint("loyverse_id", name="uq_ProductComplements_loyverse_id"),
        sa.ForeignKeyConstraint(
            ["product_modifier_id"], ["ProductModifiers.product_modifier_id"],
            name="fk_ProductComplements_product_modifier_id",
        ),
    )
    op.create_table(
        "ProductModifierAssignments",
        sa.Column("product_variant_id", sa.Integer(), nullable=False),
        sa.Column("product_modifier_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "product_variant_id", "product_modifier_id", name="pk_ProductModifierAssignments"
        ),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["ProductVariants.product_variant_id"],
            name="fk_ProductModifierAssignments_product_variant_id",
        ),
        sa.ForeignKeyConstraint(
            ["product_modifier_id"], ["ProductModifiers.product_modifier_id"],
            name="fk_ProductModifierAssignments_product_modifier_id",
        ),
    )


def downgrade() -> None:
    """Destructivo: elimina el catálogo y sus datos. Requiere autorización."""
    op.drop_table("ProductModifierAssignments")
    op.drop_table("ProductComplements")
    op.drop_table("ProductVariants")
    op.drop_table("ProductModifiers")
    op.drop_table("Products")

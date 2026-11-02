"""Fotografías y etiquetas locales según el diagrama aprobado de HU-005.

Revision ID: 20261102_0002
Revises: 20261029_0001

Los IDs enteros usan secuencias como HU-008. Las FK omiten ON DELETE y
ON UPDATE: PostgreSQL conserva NO ACTION. Aplicar en mitbol_dev requiere
autorización expresa; preparar esta revisión no modifica la base.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261102_0002"
down_revision = "20261029_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ProductImages",
        sa.Column("product_image_id", sa.Integer(), nullable=False),
        sa.Column("product_variant_id", sa.Integer(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("product_image_id", name="pk_ProductImages"),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["ProductVariants.product_variant_id"],
            name="fk_ProductImages_product_variant_id",
        ),
    )
    op.create_table(
        "Tags",
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("tag_id", name="pk_Tags"),
        sa.UniqueConstraint("name", name="uq_Tags_name"),
    )
    op.create_table(
        "ProductTags",
        sa.Column("product_variant_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("product_variant_id", "tag_id", name="pk_ProductTags"),
        sa.ForeignKeyConstraint(
            ["product_variant_id"], ["ProductVariants.product_variant_id"],
            name="fk_ProductTags_product_variant_id",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"], ["Tags.tag_id"], name="fk_ProductTags_tag_id",
        ),
    )


def downgrade() -> None:
    """Elimina sólo el enriquecimiento y sus datos; requiere autorización."""
    op.drop_table("ProductTags")
    op.drop_table("Tags")
    op.drop_table("ProductImages")

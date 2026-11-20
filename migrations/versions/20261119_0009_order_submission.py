"""HU-021: solicitudes transaccionales y recepción del restaurante, sin grants."""

from alembic import op
import sqlalchemy as sa

revision = "20261119_0009"
down_revision = "20261115_0008"
branch_labels = None
depends_on = None


def upgrade():
    settings = op.create_table(
        "RestaurantOrderSettings",
        sa.Column("settings_id", sa.SmallInteger(), nullable=False, autoincrement=False),
        sa.Column("accepting_orders", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("settings_id", name="pk_RestaurantOrderSettings"),
        sa.CheckConstraint("settings_id = 1", name="ck_RestaurantOrderSettings_singleton"),
    )
    op.bulk_insert(settings, [{"settings_id": 1, "accepting_orders": False}])
    op.create_table(
        "Orders",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("modality", sa.String(20), nullable=False),
        sa.Column("requester_name", sa.String(100), nullable=False),
        sa.Column("requester_phone", sa.String(20), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="Solicitud recibida"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("subtotal", sa.Numeric(), nullable=False),
        sa.Column("discount_total", sa.Numeric(), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("order_id", name="pk_Orders"),
        sa.UniqueConstraint("idempotency_key", name="uq_Orders_idempotency_key"),
        sa.ForeignKeyConstraint(["account_id"], ["PersonalAccounts.account_id"], name="fk_Orders_account_id", ondelete="RESTRICT"),
        sa.CheckConstraint("modality IN ('dine_in', 'pickup')", name="ck_Orders_modality"),
        sa.CheckConstraint("length(btrim(requester_name)) > 0", name="ck_Orders_name"),
        sa.CheckConstraint("(modality = 'dine_in' AND requester_phone IS NULL) OR "
                           "(modality = 'pickup' AND requester_phone IS NOT NULL AND requester_phone ~ '^\\+?[0-9]{7,15}$')",
                           name="ck_Orders_contact"),
        sa.CheckConstraint("subtotal >= 0 AND subtotal < 'Infinity'::numeric AND discount_total >= 0 "
                           "AND discount_total <= subtotal AND total = subtotal - discount_total", name="ck_Orders_amounts"),
        sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_Orders_request_hash"),
    )
    op.create_table(
        "OrderDetails",
        sa.Column("order_detail_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("line_index", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("product_variant_id", sa.Integer(), nullable=False),
        sa.Column("product_name", sa.String(150), nullable=False),
        sa.Column("variant_name", sa.String(150), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("base_price", sa.Numeric(), nullable=False),
        sa.Column("complements_total", sa.Numeric(), nullable=False),
        sa.Column("unit_total", sa.Numeric(), nullable=False),
        sa.Column("subtotal", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("order_detail_id", name="pk_OrderDetails"),
        sa.ForeignKeyConstraint(["order_id"], ["Orders.order_id"], name="fk_OrderDetails_order_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["Products.product_id"], name="fk_OrderDetails_product_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_variant_id"], ["ProductVariants.product_variant_id"], name="fk_OrderDetails_product_variant_id", ondelete="RESTRICT"),
        sa.CheckConstraint("line_index >= 0", name="ck_OrderDetails_line_index"),
        sa.CheckConstraint("quantity > 0 AND quantity < 'Infinity'::numeric AND quantity = trunc(quantity)", name="ck_OrderDetails_quantity"),
        sa.CheckConstraint("base_price >= 0 AND base_price < 'Infinity'::numeric AND complements_total >= 0 "
                           "AND complements_total < 'Infinity'::numeric AND unit_total = base_price + complements_total "
                           "AND subtotal = unit_total * quantity", name="ck_OrderDetails_amounts"),
    )
    op.create_index("uq_OrderDetails_order_line", "OrderDetails", ["order_id", "line_index"], unique=True)
    op.create_table(
        "OrderDetailComplements",
        sa.Column("order_detail_complement_id", sa.BigInteger(), nullable=False),
        sa.Column("order_detail_id", sa.BigInteger(), nullable=False),
        sa.Column("product_complement_id", sa.Integer(), nullable=False),
        sa.Column("product_modifier_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name_snapshot", sa.String(150), nullable=False),
        sa.Column("unit_price", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("order_detail_complement_id", name="pk_OrderDetailComplements"),
        sa.ForeignKeyConstraint(["order_detail_id"], ["OrderDetails.order_detail_id"], name="fk_OrderDetailComplements_order_detail_id", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_complement_id"], ["ProductComplements.product_complement_id"], name="fk_OrderDetailComplements_product_complement_id", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_modifier_id"], ["ProductModifiers.product_modifier_id"], name="fk_OrderDetailComplements_product_modifier_id", ondelete="RESTRICT"),
        sa.CheckConstraint("position >= 0", name="ck_OrderDetailComplements_position"),
        sa.CheckConstraint("unit_price >= 0 AND unit_price < 'Infinity'::numeric", name="ck_OrderDetailComplements_price"),
    )
    op.create_index("uq_OrderDetailComplements_detail_option", "OrderDetailComplements", ["order_detail_id", "product_complement_id"], unique=True)


def downgrade():
    op.drop_table("OrderDetailComplements")
    op.drop_table("OrderDetails")
    op.drop_table("Orders")
    op.drop_table("RestaurantOrderSettings")

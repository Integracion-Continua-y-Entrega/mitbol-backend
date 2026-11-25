"""Solicitudes e importes históricos; registro separado del catálogo histórico."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer,
    MetaData, Numeric, SmallInteger, String, Uuid, false, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.auth import PersonalAccount
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.product_complement import ProductComplement
from app.models.product_modifier import ProductModifier


INITIAL_STATUS = "Solicitud recibida"
CONFIRMED_STATUS = "Confirmado"
LEGACY_CONFIRMED_STATUS = "Aceptado"


class OrderBase(DeclarativeBase):
    metadata = MetaData(naming_convention=dict(Base.metadata.naming_convention))


class RestaurantOrderSettings(OrderBase):
    __tablename__ = "RestaurantOrderSettings"
    __table_args__ = (CheckConstraint("settings_id = 1", name="ck_RestaurantOrderSettings_singleton"),)

    settings_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    accepting_orders: Mapped[bool] = mapped_column(Boolean, server_default=false())


class Order(OrderBase):
    __tablename__ = "Orders"
    __table_args__ = (
        CheckConstraint("modality IN ('dine_in', 'pickup')", name="ck_Orders_modality"),
        CheckConstraint("length(btrim(requester_name)) > 0", name="ck_Orders_name"),
        CheckConstraint("(modality = 'dine_in' AND requester_phone IS NULL) OR "
                        "(modality = 'pickup' AND requester_phone IS NOT NULL AND requester_phone ~ '^\\+?[0-9]{7,15}$')",
                        name="ck_Orders_contact"),
        CheckConstraint("subtotal >= 0 AND subtotal < 'Infinity'::numeric AND discount_total >= 0 "
                        "AND discount_total <= subtotal AND total = subtotal - discount_total",
                        name="ck_Orders_amounts"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_Orders_request_hash"),
        CheckConstraint("guest_tracking_token IS NULL OR (account_id IS NULL AND "
                        "guest_tracking_token ~ '^[A-Za-z0-9_-]{43}$')", name="ck_Orders_guest_tracking_token"),
    )

    order_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    # Acceso privado recuperable en reintentos idempotentes; nunca incluir en URLs o logs.
    guest_tracking_token: Mapped[str | None] = mapped_column(String(43), unique=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey(PersonalAccount.account_id, ondelete="RESTRICT"))
    modality: Mapped[str] = mapped_column(String(20))
    requester_name: Mapped[str] = mapped_column(String(100))
    requester_phone: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(50), server_default=INITIAL_STATUS)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    subtotal: Mapped[Decimal] = mapped_column(Numeric)
    discount_total: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric)

    details: Mapped[list["OrderDetail"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderDetail.line_index")


class OrderDetail(OrderBase):
    __tablename__ = "OrderDetails"
    __table_args__ = (
        Index("uq_OrderDetails_order_line", "order_id", "line_index", unique=True),
        CheckConstraint("line_index >= 0", name="ck_OrderDetails_line_index"),
        CheckConstraint("quantity > 0 AND quantity < 'Infinity'::numeric AND quantity = trunc(quantity)",
                        name="ck_OrderDetails_quantity"),
        CheckConstraint("base_price >= 0 AND base_price < 'Infinity'::numeric AND complements_total >= 0 "
                        "AND complements_total < 'Infinity'::numeric AND unit_total = base_price + complements_total "
                        "AND subtotal = unit_total * quantity", name="ck_OrderDetails_amounts"),
    )

    order_detail_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[UUID] = mapped_column(ForeignKey("Orders.order_id", ondelete="CASCADE"))
    line_index: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[int] = mapped_column(ForeignKey(Product.product_id, ondelete="RESTRICT"))
    product_variant_id: Mapped[int] = mapped_column(ForeignKey(ProductVariant.product_variant_id, ondelete="RESTRICT"))
    product_name: Mapped[str] = mapped_column(String(150))
    variant_name: Mapped[str] = mapped_column(String(150))
    # Numeric conserva todas las cantidades enteras positivas admitidas por HU-016.
    quantity: Mapped[Decimal] = mapped_column(Numeric)
    base_price: Mapped[Decimal] = mapped_column(Numeric)
    complements_total: Mapped[Decimal] = mapped_column(Numeric)
    unit_total: Mapped[Decimal] = mapped_column(Numeric)
    subtotal: Mapped[Decimal] = mapped_column(Numeric)

    order: Mapped[Order] = relationship(back_populates="details")
    complements: Mapped[list["OrderDetailComplement"]] = relationship(
        back_populates="detail", cascade="all, delete-orphan", order_by="OrderDetailComplement.position")


class OrderDetailComplement(OrderBase):
    __tablename__ = "OrderDetailComplements"
    __table_args__ = (
        Index("uq_OrderDetailComplements_detail_option", "order_detail_id", "product_complement_id", unique=True),
        CheckConstraint("position >= 0", name="ck_OrderDetailComplements_position"),
        CheckConstraint("unit_price >= 0 AND unit_price < 'Infinity'::numeric", name="ck_OrderDetailComplements_price"),
    )

    order_detail_complement_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_detail_id: Mapped[int] = mapped_column(ForeignKey("OrderDetails.order_detail_id", ondelete="CASCADE"))
    product_complement_id: Mapped[int] = mapped_column(ForeignKey(ProductComplement.product_complement_id, ondelete="RESTRICT"))
    product_modifier_id: Mapped[int] = mapped_column(ForeignKey(ProductModifier.product_modifier_id, ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(Integer)
    name_snapshot: Mapped[str] = mapped_column(String(150))
    unit_price: Mapped[Decimal] = mapped_column(Numeric)

    detail: Mapped[OrderDetail] = relationship(back_populates="complements")

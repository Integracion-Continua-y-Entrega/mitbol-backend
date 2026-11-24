"""Consulta propia sin datos de contacto, tokens o identidades del personal."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.menu import MenuAmount
from app.schemas.orders import OrderTrackingResponse


class CustomerOrderListItem(BaseModel):
    order_id: UUID
    status: str
    created_at: datetime
    modality: Literal["dine_in", "pickup"]
    currency: Literal["MXN"] = "MXN"
    subtotal: MenuAmount
    discount_total: MenuAmount
    total: MenuAmount
    can_cancel: bool


class CustomerOrderListResponse(BaseModel):
    items: list[CustomerOrderListItem]
    total: int
    limit: int
    offset: int


class CustomerOrderDetail(OrderTrackingResponse):
    can_cancel: bool

"""Contrato administrativo de descuentos comerciales almacenados."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.services.discount_rules import DiscountType


class DiscountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    discount_id: int
    loyverse_id: str
    name: str
    discount_type: DiscountType
    value: Decimal | None
    scope: str
    is_active: bool
    synced_at: datetime | None
    created_at: datetime

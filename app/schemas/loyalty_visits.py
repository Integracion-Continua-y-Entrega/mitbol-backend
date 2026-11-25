"""Comprobante administrativo de visita; no publica la credencial ni su hash."""

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LoyaltyVisitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    visit_id: UUID
    account_id: int
    visited_on: date
    registered_at: datetime
    restaurant_timezone: str
    registered_by_account_id: int

"""Contrato mínimo de una meta y de las condiciones de su futura recompensa."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.schemas.coupons import CouponConditions


class LoyaltyGoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    required_visits: Annotated[int, Field(ge=1, le=2147483647)]
    level_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    reward_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    reward_description: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=1000)] = None
    discount_id: Annotated[int, Field(ge=1, le=2147483647)]
    valid_days: Annotated[int, Field(ge=1, le=32767)]
    conditions: CouponConditions
    is_active: bool = False

    @field_validator("level_name", "reward_name")
    @classmethod
    def visible_name(cls, value):
        """Los nombres admiten Unicode visible; los controles no forman parte de una etiqueta."""
        if not value.isprintable():
            raise ValueError("El nombre debe contener texto visible.")
        return value

    @field_validator("reward_description")
    @classmethod
    def empty_description(cls, value):
        return value or None


class LoyaltyGoalRead(LoyaltyGoalInput):
    model_config = ConfigDict(from_attributes=True)

    goal_id: UUID
    version: int
    created_at: datetime
    updated_at: datetime
    updated_by_account_id: int

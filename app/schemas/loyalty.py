"""Contrato mínimo de una meta y de las condiciones de su futura recompensa."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, field_validator, model_validator

from app.schemas.coupons import CouponConditions


class LoyaltyGoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

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
        if value is not None and "\x00" in value:
            raise ValueError("La descripción no admite caracteres nulos.")
        return value or None

    @model_validator(mode="after")
    def unambiguous_references(self):
        """Una referencia repetida o el propio descuento no define una compatibilidad adicional."""
        for values in (self.conditions.product_ids, self.conditions.compatible_discount_ids, self.conditions.modalities):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("Las referencias y modalidades no deben repetirse.")
        if self.discount_id in (self.conditions.compatible_discount_ids or []):
            raise ValueError("El descuento de la recompensa no puede declararse compatible consigo mismo.")
        return self


class LoyaltyGoalUpdate(LoyaltyGoalInput):
    version: PositiveInt


class LoyaltyGoalVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    version: PositiveInt


class LoyaltyGoalRead(LoyaltyGoalInput):
    model_config = ConfigDict(from_attributes=True)

    goal_id: UUID
    version: int
    created_at: datetime
    updated_at: datetime
    updated_by_account_id: int


class LoyaltyGoalList(BaseModel):
    items: list[LoyaltyGoalRead]
    total: int
    limit: int
    offset: int

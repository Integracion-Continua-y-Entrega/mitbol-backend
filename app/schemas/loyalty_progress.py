"""Progreso según la configuración activa; las metas pendientes todavía no son cupones."""

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.coupons import CouponConditions


class LoyaltyLevelRead(BaseModel):
    goal_id: UUID
    level_name: str
    required_visits: int = Field(ge=1)


class LoyaltyProgressGoal(LoyaltyLevelRead):
    remaining_visits: int = Field(ge=0)
    reward_name: str
    reward_description: str | None
    valid_days: int = Field(ge=1)
    conditions: CouponConditions


class LoyaltyProgressRead(BaseModel):
    total_visits: int = Field(ge=0)
    current_level: LoyaltyLevelRead | None
    next_goal: LoyaltyProgressGoal | None
    pending_goals: list[LoyaltyProgressGoal]

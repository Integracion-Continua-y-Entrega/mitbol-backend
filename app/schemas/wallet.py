"""Billetera de consulta: el estado mostrado no ejecuta reservas ni canjes."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.coupons import CouponRead


class WalletLoyaltyReward(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reward_id: UUID
    goal_id: UUID
    goal_version: int
    required_visits: int
    total_visits: int
    awarded_at: datetime


class WalletCouponRead(CouponRead):
    display_status: Literal["available", "reserved", "redeemed", "expired", "scheduled"]
    source: Literal["coupon", "loyalty_reward"]
    loyalty_reward: WalletLoyaltyReward | None


class WalletRead(BaseModel):
    coupons: list[WalletCouponRead]
    limit: int
    offset: int
    has_more: bool
    can_redeem: Literal[False] = False

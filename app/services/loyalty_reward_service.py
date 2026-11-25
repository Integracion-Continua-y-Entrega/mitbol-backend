"""Metas no recurrentes y emisión atómica de cupones al registrar presencia."""

from collections.abc import Iterable
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, text

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, LoyaltyGoal, LoyaltyReward, LoyaltyVisit
from app.schemas.loyalty import LoyaltyGoalRead
from app.schemas.loyalty_visits import LoyaltyRewardGranted
from app.services import loyalty_configuration_service as configuration


def eligible_goals(*, total_visits: int, goals: Iterable[LoyaltyGoalRead],
                   awarded_goal_ids: Iterable[UUID]) -> list[LoyaltyGoalRead]:
    """Selecciona metas alcanzadas sin reiniciar visitas ni repetir una identidad concedida.

    Evalúa el total durable, no solo el cruce exacto del umbral: una meta activada
    después de alcanzarlo puede evaluarse en la siguiente visita. Cambiar su
    versión no crea una nueva oportunidad para una meta ya concedida.
    El llamador debe verificar las referencias comerciales antes de emitir y
    garantizar unicidad en la base de datos; esta función no concede beneficios.
    """
    if type(total_visits) is not int or total_visits < 0:
        raise ValueError("El total de visitas debe ser un entero no negativo.")
    awarded = set(awarded_goal_ids)
    candidates = []
    seen = set()
    for goal in goals:
        if not isinstance(goal, LoyaltyGoalRead):
            raise ValueError("Las metas deben tener una configuración validada.")
        if goal.goal_id in seen:
            raise ValueError("La evaluación requiere una sola versión por meta.")
        seen.add(goal.goal_id)
        if goal.is_active and goal.required_visits <= total_visits and goal.goal_id not in awarded:
            candidates.append(goal)
    return sorted(candidates, key=lambda goal: (goal.required_visits, goal.goal_id))


def require_reward_schema(db):
    """Impide confirmar visitas sin evaluar recompensas por una migración faltante."""
    if not db.scalar(text("""SELECT to_regclass('public."LoyaltyRewards"') IS NOT NULL""")):
        raise AuthError(503, "El registro con recompensas requiere la migración 0019.", code="REWARDS_UNAVAILABLE")


def award_visit_rewards(db, visit: LoyaltyVisit) -> tuple[int, list[LoyaltyRewardGranted]]:
    """Cuenta evidencia durable y emite cupones en la transacción del QR y la visita.

    El registro conserva la exclusión por cuenta adquirida al consumir el QR.
    El bloqueo de catálogo evita evaluar referencias o metas mientras se editan.
    Ningún fallo se captura para confirmar parcialmente la visita.
    """
    require_reward_schema(db)
    configuration._mutation_lock(db)
    total = db.scalar(select(func.count()).select_from(LoyaltyVisit).where(LoyaltyVisit.account_id == visit.account_id))
    awarded = db.scalars(select(LoyaltyReward.goal_id).where(LoyaltyReward.account_id == visit.account_id)).all()
    goals = [configuration._read(row) for row in db.scalars(select(LoyaltyGoal).where(LoyaltyGoal.is_active))]
    rewards = []
    for goal in eligible_goals(total_visits=total, goals=goals, awarded_goal_ids=awarded):
        try:
            configuration._references(db, goal)
        except AuthError as error:
            if error.status_code not in (404, 409):
                raise
            raise AuthError(409, "Una recompensa alcanzada requiere revisar sus referencias comerciales.",
                            code="REWARD_REFERENCE_UNAVAILABLE") from None
        coupon = CustomerCoupon(account_id=visit.account_id, name=goal.reward_name, description=goal.reward_description,
            discount_id=goal.discount_id, starts_at=visit.registered_at,
            expires_at=visit.registered_at + timedelta(days=goal.valid_days), status="available",
            conditions=goal.conditions.model_dump(mode="json"), created_at=visit.registered_at)
        db.add(coupon)
        db.flush()
        reward = LoyaltyReward(account_id=visit.account_id, goal_id=goal.goal_id, visit_id=visit.visit_id,
            customer_coupon_id=coupon.customer_coupon_id, goal_version=goal.version, required_visits=goal.required_visits,
            total_visits=total, awarded_at=visit.registered_at, goal_snapshot=goal.model_dump(mode="json"))
        db.add(reward)
        db.flush()
        rewards.append(LoyaltyRewardGranted(reward_id=reward.reward_id, goal_id=goal.goal_id,
            customer_coupon_id=coupon.customer_coupon_id, name=coupon.name, expires_at=coupon.expires_at))
    return total, rewards

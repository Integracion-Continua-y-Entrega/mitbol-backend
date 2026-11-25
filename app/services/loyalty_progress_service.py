"""Consulta de progreso sin registrar presencia ni generar recompensas."""

from pydantic import ValidationError
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError
from app.models import LoyaltyGoal, LoyaltyReward, LoyaltyVisit
from app.schemas.loyalty import LoyaltyGoalRead
from app.schemas.loyalty_progress import LoyaltyLevelRead, LoyaltyProgressGoal, LoyaltyProgressRead


def get_progress(db: Session, *, account_id: int) -> LoyaltyProgressRead:
    """Lee total, metas y concesiones con una sola instantánea SQL para la cuenta.

    El llamador obtiene la identidad de su sesión, nunca de parámetros del cliente.
    El nivel refleja la configuración activa actual; editarla no altera visitas
    ni concesiones históricas. Alcanzar un umbral no prueba que exista un cupón.
    """
    if type(account_id) is not int or account_id <= 0:
        raise AuthError(422, "Cuenta inválida para consultar progreso.", code="LOYALTY_PROGRESS_INPUT")
    if not db.scalar(text("""SELECT to_regclass('public."LoyaltyVisits"') IS NOT NULL
        AND to_regclass('public."LoyaltyGoals"') IS NOT NULL
        AND to_regclass('public."LoyaltyRewards"') IS NOT NULL""")):
        raise AuthError(503, "La consulta de progreso requiere la migración 0019.", code="LOYALTY_PROGRESS_UNAVAILABLE")
    visits = select(func.count().label("total_visits")).select_from(LoyaltyVisit).where(
        LoyaltyVisit.account_id == account_id).subquery()
    query = select(visits.c.total_visits, LoyaltyGoal, LoyaltyReward.reward_id).select_from(visits).outerjoin(
        LoyaltyGoal, LoyaltyGoal.is_active.is_(True)).outerjoin(LoyaltyReward, and_(
            LoyaltyReward.goal_id == LoyaltyGoal.goal_id, LoyaltyReward.account_id == account_id)
        ).order_by(LoyaltyGoal.required_visits, LoyaltyGoal.goal_id).execution_options(populate_existing=True)
    total, current, upcoming, pending = 0, None, None, []
    for total, row, reward_id in db.execute(query):
        if row is None:
            continue
        try:
            goal = LoyaltyGoalRead.model_validate(row)
        except ValidationError:
            raise AuthError(503, "La configuración de fidelización requiere revisión.", code="LOYALTY_PROGRESS_CONFIGURATION") from None
        if goal.required_visits <= total:
            current = LoyaltyLevelRead(goal_id=goal.goal_id, level_name=goal.level_name, required_visits=goal.required_visits)
        if reward_id is not None:
            continue
        detail = LoyaltyProgressGoal(goal_id=goal.goal_id, level_name=goal.level_name,
            required_visits=goal.required_visits, remaining_visits=max(0, goal.required_visits - total),
            reward_name=goal.reward_name, reward_description=goal.reward_description,
            valid_days=goal.valid_days, conditions=goal.conditions)
        if detail.remaining_visits == 0:
            pending.append(detail)
        elif upcoming is None:
            upcoming = detail
    return LoyaltyProgressRead(total_visits=total, current_level=current, next_goal=upcoming, pending_goals=pending)

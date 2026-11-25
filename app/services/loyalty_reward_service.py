"""Evaluación de metas no recurrentes; la persistencia confirmará las concesiones."""

from collections.abc import Iterable
from uuid import UUID

from app.schemas.loyalty import LoyaltyGoalRead


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

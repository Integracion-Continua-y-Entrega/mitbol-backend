"""Reglas de selección de recompensas; no requieren sesiones ni datos reales."""

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.schemas.loyalty import LoyaltyGoalRead
from app.services.loyalty_reward_service import eligible_goals


def goal(number=1, **changes):
    point = datetime(2026, 11, 25, tzinfo=timezone.utc)
    return LoyaltyGoalRead.model_validate({
        "goal_id": UUID(int=number), "required_visits": 5,
        "level_name": "Frecuente", "reward_name": "Beneficio",
        "discount_id": 1, "valid_days": 30, "conditions": {"scope": "order"},
        "is_active": True, "version": 1, "created_at": point, "updated_at": point,
        "updated_by_account_id": 1, **changes,
    })


@pytest.mark.parametrize("total,expected", [(0, []), (4, []), (5, [1]), (6, [1]), (100, [1])])
def test_threshold_uses_accumulated_total_including_previously_reached_goals(total, expected):
    result = eligible_goals(total_visits=total, goals=[goal()], awarded_goal_ids=[])
    assert [item.goal_id.int for item in result] == expected


def test_all_reached_active_goals_are_ordered_without_resetting_progress():
    goals = [goal(3, required_visits=10), goal(1, required_visits=3),
             goal(2, required_visits=5), goal(4, required_visits=2, is_active=False),
             goal(5, required_visits=20)]
    before = [item.model_dump() for item in goals]
    assert [item.goal_id.int for item in eligible_goals(
        total_visits=12, goals=iter(goals), awarded_goal_ids=[])] == [1, 2, 3]
    assert [item.model_dump() for item in goals] == before


def test_awarded_identity_cannot_be_reissued_after_configuration_changes():
    updated = goal(version=8, required_visits=2, reward_name="Nueva configuración")
    assert eligible_goals(total_visits=50, goals=[updated], awarded_goal_ids=[updated.goal_id]) == []


def test_awards_for_other_goals_do_not_hide_a_new_eligible_goal():
    assert eligible_goals(total_visits=5, goals=[goal()], awarded_goal_ids=[UUID(int=2)]) == [goal()]


@pytest.mark.parametrize("total", [-1, True, False, 1.0, "5", None])
def test_invalid_totals_are_rejected(total):
    with pytest.raises(ValueError):
        eligible_goals(total_visits=total, goals=[], awarded_goal_ids=[])


def test_conflicting_versions_of_the_same_identity_are_rejected():
    with pytest.raises(ValueError):
        eligible_goals(total_visits=10, goals=[goal(), goal(version=2)], awarded_goal_ids=[])


def test_unvalidated_configuration_is_not_used():
    with pytest.raises(ValueError):
        eligible_goals(total_visits=10, goals=[{"required_visits": 1}], awarded_goal_ids=[])

"""HU-047: progreso real, aislamiento por cuenta y lecturas sin concesiones."""

from datetime import timedelta
from unittest.mock import Mock

from alembic import command
import pytest
from sqlalchemy import event, select

from app.core.auth_errors import AuthError
from app.models import CustomerCoupon, LoyaltyGoal, LoyaltyReward, LoyaltyVisit
from app.services.loyalty_progress_service import get_progress
from test_auth_persistence import config_for
from test_loyalty_configuration_persistence import goal
from test_loyalty_reward_persistence import (
    auth_backend, auth_database, backend, configuration_backend, coupon_backend,
    order_backend, password_hash, payment_backend,
)
from test_loyalty_visit_persistence import POINT, record


def visits(b, count, role="customer", *, offset=0):
    with b.sessions.begin() as db:
        rows = [record(b, account_id=b.ids[role][0], registered_at=POINT + timedelta(days=day),
            visited_on=(POINT + timedelta(days=day)).date()) for day in range(offset, offset + count)]
        db.add_all(rows)
        db.flush()
        return [row.visit_id for row in rows]


def goals(b, thresholds=(3, 5, 10), **changes):
    with b.sessions.begin() as db:
        rows = [goal(b, required_visits=threshold, level_name=f"Nivel {threshold}", is_active=True, **changes)
                for threshold in thresholds]
        db.add_all(rows)
        db.flush()
        return [row.goal_id for row in rows]


def read(b, role="customer"):
    with b.sessions() as db:
        return get_progress(db, account_id=b.ids[role][0])


def test_empty_program_does_not_invent_a_level_or_a_goal(backend):
    assert read(backend).model_dump() == {"total_visits": 0, "current_level": None,
        "next_goal": None, "pending_goals": []}


@pytest.mark.parametrize("count,level,next_threshold,pending", [
    (0, None, 3, []), (2, None, 3, []), (3, 3, 5, [3]),
    (7, 5, 10, [3, 5]), (10, 10, None, [3, 5, 10]),
])
def test_levels_and_remaining_visits_follow_real_thresholds(backend, count, level, next_threshold, pending):
    b = backend
    goals(b)
    visits(b, count)
    result = read(b)
    assert result.total_visits == count
    assert (result.current_level.required_visits if result.current_level else None) == level
    assert (result.next_goal.required_visits if result.next_goal else None) == next_threshold
    if result.next_goal:
        assert result.next_goal.remaining_visits == next_threshold - count
        assert result.next_goal.reward_name == "Recompensa sintética"
        assert result.next_goal.valid_days == 30 and result.next_goal.conditions.scope == "order"
    assert [row.required_visits for row in result.pending_goals] == pending
    assert all(row.remaining_visits == 0 for row in result.pending_goals)


def test_visits_and_awards_of_another_account_do_not_affect_progress(backend):
    b = backend
    identifiers = goals(b, (1, 5))
    foreign = visits(b, 6, role="admin")
    visits(b, 2)
    with b.sessions.begin() as db:
        db.add(LoyaltyReward(account_id=b.ids["admin"][0], goal_id=identifiers[0], visit_id=foreign[-1],
            customer_coupon_id=b.foreign_coupon.customer_coupon_id, goal_version=1, required_visits=1,
            total_visits=6, awarded_at=POINT, goal_snapshot={"source": "synthetic"}))
    own, other = read(b), read(b, "admin")
    assert own.total_visits == 2 and own.current_level.required_visits == 1
    assert [row.goal_id for row in own.pending_goals] == [identifiers[0]]
    assert own.next_goal.remaining_visits == 3
    assert other.total_visits == 6 and other.current_level.required_visits == 5
    assert [row.goal_id for row in other.pending_goals] == [identifiers[1]]


def test_awarded_goal_stays_excluded_after_raising_its_threshold(backend):
    b = backend
    identifiers = goals(b, (1, 5))
    visit_id = visits(b, 1)[0]
    with b.sessions.begin() as db:
        db.add(LoyaltyReward(account_id=b.ids["customer"][0], goal_id=identifiers[0], visit_id=visit_id,
            customer_coupon_id=b.coupon.customer_coupon_id, goal_version=1, required_visits=1,
            total_visits=1, awarded_at=POINT, goal_snapshot={"source": "synthetic"}))
    assert read(b).pending_goals == []
    with b.sessions.begin() as db:
        row = db.get(LoyaltyGoal, identifiers[0])
        row.required_visits, row.version = 3, 2
    result = read(b)
    assert result.total_visits == 1 and result.current_level is None
    assert result.next_goal.goal_id == identifiers[1] and result.next_goal.remaining_visits == 4
    assert result.pending_goals == []


def test_deactivated_goals_do_not_erase_visits_or_create_a_level(backend):
    b = backend
    identifiers = goals(b, (1,))
    visits(b, 4)
    with b.sessions.begin() as db:
        db.get(LoyaltyGoal, identifiers[0]).is_active = False
    result = read(b)
    assert result.total_visits == 4
    assert result.current_level is None and result.next_goal is None and result.pending_goals == []


def test_repeated_reads_do_not_generate_coupons_or_modify_progress(backend):
    b = backend
    goals(b, (1,))
    visits(b, 2)
    models = (LoyaltyVisit, LoyaltyGoal, LoyaltyReward, CustomerCoupon)
    def snapshot():
        with b.sessions() as db:
            return {model.__tablename__: db.execute(select(model.__table__)).all() for model in models}
    before = snapshot()
    first, second = read(b), read(b)
    assert first == second and len(first.pending_goals) == 1
    assert snapshot() == before
    public = first.model_dump_json()
    assert all(secret not in public for secret in ('"account_id":', '"updated_by_account_id":', '"discount_id":',
        '"identification_hash":', '"token_hash":'))


def test_count_and_configuration_share_a_snapshot_during_a_concurrent_visit(backend):
    b = backend
    goals(b, (1,))
    engine = b.sessions.kw["bind"]
    observed = []
    def concurrent_visit(conn, cursor, statement, parameters, context, executemany):
        if 'LEFT OUTER JOIN "LoyaltyGoals"' in statement and not observed:
            observed.append(statement)
            visits(b, 1)
    event.listen(engine, "after_cursor_execute", concurrent_visit)
    try:
        result = read(b)
    finally:
        event.remove(engine, "after_cursor_execute", concurrent_visit)
    assert len(observed) == 1
    assert result.total_visits == 0 and result.current_level is None
    assert result.next_goal.remaining_visits == 1 and result.pending_goals == []
    assert read(b).total_visits == 1


def test_unknown_stored_conditions_fail_without_making_up_progress(backend):
    b = backend
    identifier = goals(b, (1,))[0]
    with b.sessions.begin() as db:
        db.get(LoyaltyGoal, identifier).conditions = {"unexpected": True}
    with pytest.raises(AuthError) as error:
        read(b)
    assert error.value.status_code == 503


def test_missing_reward_migration_is_reported_as_unavailable(backend):
    b = backend
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261125_0018")
    try:
        with pytest.raises(AuthError) as error:
            read(b)
        assert error.value.status_code == 503
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261125_0019")


@pytest.mark.parametrize("identifier", [0, -1, True, "1", None])
def test_invalid_account_is_rejected_before_querying(identifier):
    db = Mock()
    with pytest.raises(AuthError):
        get_progress(db, account_id=identifier)
    assert db.mock_calls == []

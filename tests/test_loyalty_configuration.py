"""HU-046: administración por Admin, referencias verificadas y ediciones con versión."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import Mock
from uuid import UUID, uuid4

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.main import create_app
from app.models import CustomerCoupon, Discount, Employee, LoyaltyGoal, Product
from app.schemas.loyalty import LoyaltyGoalUpdate
from app.services import auth_service, loyalty_configuration_service as service
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY
from test_auth_api import csrf_headers, login, ORIGIN
from test_auth_persistence import config_for
from test_loyalty_configuration_persistence import (
    auth_backend, auth_database, backend, coupon_backend, order_backend, payment_backend, password_hash, payload,
)


PATH = "/api/admin/loyalty/goals"


def body(b, **changes):
    return payload(discount_id=b.discount.discount_id, **changes)


def create(b, **changes):
    headers = csrf_headers(login(b, "admin"))
    response = b.client.post(PATH, headers=headers, json=body(b, **changes))
    assert response.status_code == 201, response.text
    return response.json(), headers


def test_create_read_update_and_deactivate_preserve_identity_and_do_not_issue_rewards(backend):
    b = backend
    with b.sessions() as db:
        coupons = db.execute(select(CustomerCoupon.__table__)).all()
    created, headers = create(b, level_name="  Nivel revisado  ", is_active=True)
    target = f'{PATH}/{created["goal_id"]}'
    assert created["level_name"] == "Nivel revisado" and created["version"] == 1
    assert created["updated_by_account_id"] == b.ids["admin"][0]
    read = b.client.get(target)
    assert read.status_code == 200 and read.json() == created
    assert read.headers["cache-control"] == "no-store"
    update = body(b, required_visits=7, reward_name="Nueva recompensa", is_active=True) | {"version": 1}
    edited = b.client.put(target, headers=headers, json=update)
    assert edited.status_code == 200, edited.text
    current = edited.json()
    assert current["goal_id"] == created["goal_id"] and current["version"] == 2
    assert current["created_at"] == created["created_at"] and current["updated_at"] >= created["updated_at"]
    assert current["required_visits"] == 7 and current["reward_name"] == "Nueva recompensa"
    assert b.client.put(target, headers=headers, json=update).status_code == 409
    with b.sessions.begin() as db:
        db.get(Discount, b.discount.discount_id).is_active = False
    disabled = b.client.post(target + "/deactivate", headers=headers, json={"version": 2})
    assert disabled.status_code == 200 and disabled.json()["is_active"] is False
    assert disabled.json()["version"] == 3
    assert b.client.post(target + "/deactivate", headers=headers, json={"version": 2}).status_code == 409
    repeated = b.client.post(target + "/deactivate", headers=headers, json={"version": 3})
    assert repeated.json() == disabled.json()
    assert b.client.delete(target, headers=headers).status_code in {404, 405}
    with b.sessions() as db:
        assert db.execute(select(CustomerCoupon.__table__)).all() == coupons


def test_list_filters_sort_and_pagination_do_not_mutate_configuration(backend):
    b = backend
    for visits, active in ((10, False), (3, True), (7, True)):
        create(b, required_visits=visits, is_active=active)
    page = b.client.get(PATH, params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and page["items"][0]["required_visits"] == 7
    active = b.client.get(PATH, params={"is_active": True}).json()
    assert active["total"] == 2 and [row["required_visits"] for row in active["items"]] == [3, 7]
    inactive = b.client.get(PATH, params={"is_active": False}).json()
    assert inactive["total"] == 1 and inactive["items"][0]["required_visits"] == 10
    assert b.client.get(PATH, params={"offset": 100}).json()["items"] == []
    assert all(row["version"] == 1 for row in b.client.get(PATH).json()["items"])
    for query in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"is_active": "invalid"}):
        assert b.client.get(PATH, params=query).status_code == 422


@pytest.mark.parametrize("role", [None, "customer", "staff"])
def test_only_admin_can_read_or_change_configuration(backend, role):
    b = backend
    created, _ = create(b)
    b.client.cookies.clear()
    headers = csrf_headers(login(b, role)) if role else {"Origin": ORIGIN}
    target = f'{PATH}/{created["goal_id"]}'
    responses = [b.client.get(PATH), b.client.get(target),
                 b.client.post(PATH, headers=headers, json=body(b, required_visits=9)),
                 b.client.put(target, headers=headers, json=body(b) | {"version": 1}),
                 b.client.post(target + "/deactivate", headers=headers, json={"version": 1})]
    assert {r.status_code for r in responses} == {403 if role else 401}
    with b.sessions() as db:
        assert db.get(LoyaltyGoal, UUID(created["goal_id"])).version == 1


def test_csrf_origin_inactive_employee_and_dedicated_permissions(backend, monkeypatch):
    b = backend
    created, headers = create(b)
    target = f'{PATH}/{created["goal_id"]}'
    for invalid in ({"Origin": ORIGIN}, headers | {"X-CSRF-Token": "0" * 64},
                    headers | {"Origin": "https://evil.invalid"}):
        assert b.client.post(PATH, headers=invalid, json=body(b, required_visits=9)).status_code == 403
        assert b.client.put(target, headers=invalid, json=body(b) | {"version": 1}).status_code == 403
        assert b.client.post(target + "/deactivate", headers=invalid, json={"version": 1}).status_code == 403
    monkeypatch.setattr(auth_service, "permissions_for_roles", lambda *args, **kwargs: frozenset({"catalog:read"}))
    headers = csrf_headers(login(b, "admin"))
    assert b.client.get(PATH).status_code == 403
    monkeypatch.setattr(auth_service, "permissions_for_roles", lambda *args, **kwargs: frozenset({"catalog:read", "loyalty:read"}))
    headers = csrf_headers(login(b, "admin"))
    assert b.client.get(PATH).status_code == 200
    assert b.client.post(PATH, headers=headers, json=body(b, required_visits=9)).status_code == 403
    with b.sessions.begin() as db:
        db.get(Employee, b.ids["admin"][1]).is_active = False
    assert b.client.get(PATH).status_code == 401


@pytest.mark.parametrize("changes", [
    {"required_visits": 0}, {"required_visits": True}, {"valid_days": 0}, {"level_name": " "},
    {"reward_description": "nul\x00"}, {"goal_id": str(uuid4())}, {"version": 1},
    {"updated_by_account_id": 999}, {"created_at": "2026-01-01T00:00:00Z"},
    {"conditions": {"scope": "products"}}, {"conditions": {"scope": "products", "product_ids": [1, 1], "include_complements": False}},
    {"conditions": {"scope": "order", "modalities": ["pickup", "pickup"]}},
])
def test_invalid_configuration_and_server_controlled_fields_return_422(backend, changes):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    response = b.client.post(PATH, headers=headers, json=body(b) | changes)
    assert response.status_code == 422, response.text
    with b.sessions() as db:
        assert db.scalar(select(LoyaltyGoal)) is None


@pytest.mark.parametrize("change", ["missing", "inactive", "points", "missing_value", "zero", "negative", "nan", "infinity", "percent_over_100"])
def test_discount_must_exist_and_have_an_active_calculable_value(backend, change):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    request = body(b)
    with b.sessions.begin() as db:
        discount = db.get(Discount, b.discount.discount_id)
        if change == "missing":
            request["discount_id"] = 2147483647
        elif change == "inactive":
            discount.is_active = False
        elif change == "points":
            discount.discount_type, discount.value = "DISCOUNT_BY_POINTS", None
        elif change == "missing_value":
            discount.value = None
        elif change in {"zero", "negative", "nan", "infinity"}:
            discount.value = {"zero": Decimal("0"), "negative": Decimal("-1"), "nan": Decimal("NaN"), "infinity": Decimal("Infinity")}[change]
        else:
            discount.discount_type, discount.value = "FIXED_PERCENT", Decimal("101")
    response = b.client.post(PATH, headers=headers, json=request)
    assert response.status_code == (404 if change == "missing" else 409), response.text
    with b.sessions() as db:
        assert db.scalar(select(LoyaltyGoal)) is None


@pytest.mark.parametrize("kind,value", [("FIXED_PERCENT", "100"), ("VARIABLE_PERCENT", "10"), ("FIXED_AMOUNT", "25"), ("VARIABLE_AMOUNT", "5")])
def test_supported_discounts_keep_their_original_scope_without_local_monetary_copies(backend, kind, value):
    b = backend
    with b.sessions.begin() as db:
        row = db.get(Discount, b.discount.discount_id)
        row.discount_type, row.value = kind, Decimal(value)
    created, _ = create(b)
    assert created["discount_id"] == b.discount.discount_id
    assert "discount_value" not in created
    with b.sessions() as db:
        assert db.get(Discount, b.discount.discount_id).scope == "unknown"


@pytest.mark.parametrize("change", ["missing", "inactive", "temporarily_unavailable"])
def test_product_conditions_require_active_references_but_do_not_reserve_stock(backend, change):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    identifier = b.products[0].product_id
    with b.sessions.begin() as db:
        product = db.get(Product, identifier)
        if change == "missing":
            identifier = 2147483647
        elif change == "inactive":
            product.is_active = False
        else:
            product.is_available = False
    response = b.client.post(PATH, headers=headers, json=body(b, conditions={"scope": "products", "product_ids": [identifier], "include_complements": False}))
    assert response.status_code == {"missing": 404, "inactive": 409, "temporarily_unavailable": 201}[change], response.text


def test_compatible_discounts_are_referenced_explicitly_and_validated(backend):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    conditions = {"scope": "order", "compatible_discount_ids": [b.discount.discount_id]}
    assert b.client.post(PATH, headers=headers, json=body(b, conditions=conditions)).status_code == 422
    conditions["compatible_discount_ids"] = [2147483647]
    assert b.client.post(PATH, headers=headers, json=body(b, conditions=conditions)).status_code == 404
    with b.sessions.begin() as db:
        other = Discount(loyverse_id=uuid4().hex, name="Compatible sintético", discount_type="FIXED_PERCENT", value=Decimal("5"), scope="unknown", is_active=True)
        db.add(other)
        db.flush()
        other_id = other.discount_id
    conditions["compatible_discount_ids"] = [other_id, other_id]
    assert b.client.post(PATH, headers=headers, json=body(b, conditions=conditions)).status_code == 422
    conditions["compatible_discount_ids"] = [other_id]
    result = b.client.post(PATH, headers=headers, json=body(b, conditions=conditions))
    assert result.status_code == 201, result.text
    with b.sessions.begin() as db:
        db.get(Discount, other_id).is_active = False
    assert b.client.post(PATH, headers=headers, json=body(b, required_visits=10, conditions=conditions)).status_code == 409


def test_duplicate_threshold_and_stale_version_do_not_replace_existing_configuration(backend):
    b = backend
    created, headers = create(b)
    assert b.client.post(PATH, headers=headers, json=body(b)).status_code == 409
    other, headers = create(b, required_visits=9)
    target = f'{PATH}/{other["goal_id"]}'
    collision = body(b) | {"version": 1}
    assert b.client.put(target, headers=headers, json=collision).status_code == 409
    assert b.client.get(target).json() == other
    assert b.client.put(target, headers=headers, json=body(b, required_visits=11) | {"version": 99}).status_code == 409
    assert b.client.get(target).json() == other


def test_not_found_version_validation_and_missing_migration(backend):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    target = f"{PATH}/{uuid4()}"
    assert b.client.get(target).status_code == 404
    assert b.client.put(target, headers=headers, json=body(b) | {"version": 1}).status_code == 404
    assert b.client.post(target + "/deactivate", headers=headers, json={"version": 1}).status_code == 404
    for version in (None, 0, True, "1"):
        assert b.client.put(target, headers=headers, json=body(b) | {"version": version}).status_code == 422
    assert b.client.post(target + "/deactivate", headers=headers, json={}).status_code == 422
    with b.sessions.kw["bind"].begin() as db:
        command.downgrade(config_for(db), "20261124_0015")
    try:
        assert b.client.get(PATH).status_code == 503
        assert b.client.post(PATH, headers=headers, json=body(b)).status_code == 503
    finally:
        with b.sessions.kw["bind"].begin() as db:
            command.upgrade(config_for(db), "20261124_0016")


def test_catalog_lock_conflicts_can_be_retried_and_reads_do_not_need_the_lock(backend):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    with b.sessions.begin() as db:
        db.scalar(select(func.pg_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY)))
        assert b.client.post(PATH, headers=headers, json=body(b)).status_code == 409
        assert b.client.get(PATH).status_code == 200
    assert b.client.post(PATH, headers=headers, json=body(b)).status_code == 201


def test_concurrent_edits_have_one_winner_and_one_version_increment(backend):
    b = backend
    created, _ = create(b)
    barrier = Barrier(2)
    def edit(visits):
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                return service.update_goal(db, UUID(created["goal_id"]), LoyaltyGoalUpdate.model_validate(body(b, required_visits=visits) | {"version": 1}),
                                           actor_account_id=b.ids["admin"][0]).required_visits
        except AuthError as error:
            assert error.status_code == 409
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(edit, visits) for visits in (7, 9)]
        values = [future.result(timeout=15) for future in futures]
    assert sum(value is not None for value in values) == 1
    read = b.client.get(f'{PATH}/{created["goal_id"]}').json()
    assert read["version"] == 2 and read["required_visits"] in values


@pytest.mark.parametrize("operation", ["create", "update"])
def test_sql_failure_rolls_back_and_does_not_leak_details(backend, caplog, operation):
    b = backend
    created, headers = create(b)
    target = f'{PATH}/{created["goal_id"]}'
    engine = b.service.sessions.kw["bind"]
    def fail(conn, cursor, statement, parameters, context, many):
        prefix = 'INSERT INTO "LoyaltyGoals"' if operation == "create" else 'UPDATE "LoyaltyGoals"'
        if statement.startswith(prefix):
            raise OperationalError("private-goal-error", {}, Exception("private-goal-error"))
    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = (b.client.post(PATH, headers=headers, json=body(b, required_visits=9)) if operation == "create"
                    else b.client.put(target, headers=headers, json=body(b, required_visits=9) | {"version": 1}))
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert response.status_code == 503 and "private-goal-error" not in response.text + caplog.text
    assert b.client.get(target).json() == created
    assert b.client.get(PATH).json()["total"] == 1


def test_commit_failure_after_the_goal_insert_does_not_report_success(backend):
    b = backend
    headers = csrf_headers(login(b, "admin"))
    engine, wrote = b.service.sessions.kw["bind"], []
    def capture(conn, cursor, statement, parameters, context, many):
        if statement.startswith('INSERT INTO "LoyaltyGoals"'):
            wrote.append(True)
    def fail_commit(conn):
        if wrote:
            raise OperationalError("private-commit-error", {}, Exception("private-commit-error"))
    event.listen(engine, "after_cursor_execute", capture)
    event.listen(engine, "commit", fail_commit)
    try:
        response = b.client.post(PATH, headers=headers, json=body(b))
    finally:
        event.remove(engine, "after_cursor_execute", capture)
        event.remove(engine, "commit", fail_commit)
    assert wrote and response.status_code == 503
    assert b.client.get(PATH).json()["total"] == 0
    assert b.client.post(PATH, headers=headers, json=body(b)).status_code == 201


def test_configuration_is_independent_of_catalog_admin_flag_and_has_private_cors(backend):
    b = backend
    config = b.config.model_copy(update={"auth_catalog_enabled": False, "auth_images_enabled": False})
    app = create_app(config)
    app.state.auth_service.clock = lambda: b.now.value
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        headers = csrf_headers(login(b, "admin", client=client))
        assert client.get(PATH).status_code == 200
        assert client.post(PATH, headers=headers, json=body(b)).status_code == 201
        assert client.get("/api/admin/tags").status_code == 404
        preflight = client.options(PATH, headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token"})
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"
        assert client.options(PATH, headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "POST"}).status_code == 400


def test_service_rejects_invalid_input_before_querying_the_database():
    db = Mock()
    with pytest.raises(AuthError) as error:
        service.create_goal(db, {"required_visits": 0}, actor_account_id=1)
    assert error.value.status_code == 422 and db.mock_calls == []

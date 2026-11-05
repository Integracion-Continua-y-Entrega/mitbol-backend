"""Bajas de HU-008 con datos ficticios, HTTP simulado y sesión sin PostgreSQL."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import MagicMock

import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from app.cli import sync_catalog
from app.models import Product
from app.services.catalog_errors import CatalogValidationError
from app.services.catalog_transform import transform_catalog
from app.services.loyverse_client import LoyverseAPIClient
from app.services.sync_service import SyncService


TOMBSTONES = (
    {"id": "retired-a", "deleted_at": "2026-11-02T02:00:04.000Z"},
    {"id": "retired-b", "deleted_at": "2026-11-02T02:00:04.000Z"},
)


def test_two_tombstones_are_separate_from_three_products_and_five_variants(commercial_snapshot):
    active = transform_catalog(commercial_snapshot)
    snapshot = replace(commercial_snapshot, items=(*commercial_snapshot.items, *TOMBSTONES))
    plan = transform_catalog(snapshot)
    assert plan.products == active.products and plan.variants == active.variants
    assert (len(plan.products), len(plan.variants), len(plan.assignments)) == (3, 5, 4)
    assert plan.deleted_products == ("retired-a", "retired-b")
    assert plan.deleted_variants == ()
    assert not {p.loyverse_id for p in plan.products}.intersection(plan.deleted_products)


@pytest.mark.parametrize("explicit_null", [False, True])
def test_local_inactive_flag_and_unavailable_variant_do_not_make_product_a_tombstone(
    commercial_snapshot, explicit_null,
):
    snapshot = deepcopy(commercial_snapshot)
    item = snapshot.items[0]
    item["is_active"] = False
    if explicit_null:
        item["deleted_at"] = None
    item["variants"][0]["stores"][0]["available_for_sale"] = False
    plan = transform_catalog(snapshot)
    assert len(plan.products) == 3 and plan.deleted_products == ()
    assert plan.variants[0].is_commercially_available is False


@pytest.mark.parametrize("timestamp", ["", "invalid", False, 0, "2026-11-02T02:00:04"])
def test_invalid_deleted_at_aborts_instead_of_guessing_commercial_state(commercial_snapshot, timestamp):
    snapshot = replace(commercial_snapshot, items=(
        *commercial_snapshot.items, {"id": "invalid-retirement", "deleted_at": timestamp},
    ))
    with pytest.raises(CatalogValidationError, match="deleted_at"):
        transform_catalog(snapshot)


def test_retirement_of_absent_ids_only_attempts_scoped_updates(commercial_snapshot, monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    plan = transform_catalog(replace(commercial_snapshot, items=(*commercial_snapshot.items, *TOMBSTONES)))
    sessions = MagicMock()
    service = SyncService(sessions, expected_database="mitbol_test_hu008_abcdef123456", store_id="test-store")
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    retired, removed_links = service._retire(session, plan)
    assert retired == {"products": 0, "variants": 0, "modifiers": 0, "complements": 0}
    assert removed_links == 0 and session.execute.call_count == 2
    for call, tombstone in zip(session.execute.call_args_list, TOMBSTONES):
        statement = call.args[0]
        assert isinstance(statement, Update) and statement.table.name == Product.__tablename__
        compiled = statement.compile(dialect=postgresql.dialect())
        assert compiled.params["loyverse_id_1"] == tombstone["id"]
        assert compiled.params["is_active"] is False
        assert "WHERE" in str(compiled) and "is_available" not in str(compiled)
    sessions.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_preview_reports_two_tombstones_without_persisting(commercial_snapshot, monkeypatch):
    def respond(request):
        assert request.method == "GET"
        if request.url.path.endswith("/stores"):
            return httpx.Response(200, json={"stores": [commercial_snapshot.store]})
        assert request.url.params["show_deleted"] == "true"
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"items": [*commercial_snapshot.items, *TOMBSTONES]})
        assert request.url.path.endswith("/modifiers")
        return httpx.Response(200, json={"modifiers": list(commercial_snapshot.modifiers)})

    monkeypatch.setattr(sync_catalog, "LoyverseAPIClient", lambda **kwargs: LoyverseAPIClient(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    forbidden = MagicMock(side_effect=AssertionError("Preview attempted persistence"))
    monkeypatch.setattr(SyncService, "_write", forbidden)
    report = await sync_catalog.preview_catalog("test-store", SecretStr("fake-token"))
    assert report["planned_initial_inserts"] == {
        "Products": 3, "ProductVariants": 5, "ProductModifiers": 2,
        "ProductComplements": 5, "ProductModifierAssignments": 4,
    }
    assert report["commercially_available_variants"] == 5
    assert report["tombstones"] == {"products": 2, "variants": 0, "modifiers": 0, "complements": 0}
    forbidden.assert_not_called()

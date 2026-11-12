"""Despacho asíncrono sin red, configuración o bases reales."""

from unittest.mock import AsyncMock

import pytest

from app.services.manual_sync_coordinator import ManualSyncCoordinator, ManualSyncUnavailableError


@pytest.mark.asyncio
@pytest.mark.parametrize("target,result", [("catalog", {"products_processed": 3}), ("discounts", 4)])
async def test_only_selected_runner_is_awaited_and_result_preserved(target, result):
    catalog, discounts = AsyncMock(return_value=result), AsyncMock(return_value=result)
    runner = ManualSyncCoordinator(catalog_runner=catalog, discounts_runner=discounts)
    assert await runner.execute(target) == result
    (catalog if target == "catalog" else discounts).assert_awaited_once_with()
    (discounts if target == "catalog" else catalog).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["catalog", "discounts"])
async def test_no_executor_is_disabled_by_default(target):
    with pytest.raises(ManualSyncUnavailableError):
        await ManualSyncCoordinator().execute(target)


@pytest.mark.asyncio
async def test_error_is_propagated_without_fallback_or_retry():
    catalog, discounts = AsyncMock(side_effect=RuntimeError("synthetic")), AsyncMock()
    with pytest.raises(RuntimeError):
        await ManualSyncCoordinator(catalog_runner=catalog, discounts_runner=discounts).execute("catalog")
    catalog.assert_awaited_once()
    discounts.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["all", "", None, [], True])
async def test_unknown_target_never_dispatches(target):
    catalog = AsyncMock()
    with pytest.raises(ValueError):
        await ManualSyncCoordinator(catalog_runner=catalog).execute(target)
    catalog.assert_not_called()

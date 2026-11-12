"""HU-009: respuestas y fallos simulados; nunca usa token o red reales."""

import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services.discount_probe import fetch_discount_snapshot
from app.services.discount_transform import DiscountValidationError
from app.services.loyverse_client import LoyverseAPIClient, LoyverseAPIError, LoyverseResponseError
from test_discount_transform import DISCOUNT_ID, source_discount


BASE = LoyverseAPIClient.BASE_URL


@pytest.mark.asyncio
@pytest.mark.parametrize("include_deleted", [False, True])
async def test_discounts_do_not_invent_pagination_parameters(include_deleted):
    with respx.mock() as router:
        route = router.get(f"{BASE}/discounts").respond(200, json={"discounts": []})
        async with LoyverseAPIClient("hu009-synthetic-token", page_size=1) as client:
            assert await client.get_discounts(include_deleted=include_deleted) == []
            assert client.page_counts["/discounts"] == 1
        assert route.call_count == 1
        assert dict(route.calls[0].request.url.params) == ({"show_deleted": "true"} if include_deleted else {})
        assert route.calls[0].request.method == "GET"


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["next", {}, [], True, 0])
async def test_undocumented_cursor_is_rejected_as_incomplete_collection(cursor):
    with respx.mock() as router:
        router.get(f"{BASE}/discounts").respond(200, json={"discounts": [], "cursor": cursor})
        async with LoyverseAPIClient("hu009-synthetic-token") as client:
            with pytest.raises(LoyverseResponseError):
                await client.get_discounts()
            assert "/discounts" not in client.page_counts


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"discounts": None}, {"discounts": {}}, {"discounts": [None]}])
async def test_invalid_collection_never_becomes_empty_success(response):
    with respx.mock() as router:
        router.get(f"{BASE}/discounts").respond(200, json=response)
        async with LoyverseAPIClient("hu009-synthetic-token") as client:
            with pytest.raises(LoyverseResponseError):
                await client.get_discounts()


@pytest.mark.asyncio
async def test_snapshot_keeps_exact_numeric_value_and_includes_explicit_tombstones():
    record = source_discount(discount_percent=None, deleted_at="2026-11-03T14:00:00Z")
    body = json.dumps({"discounts": [record]}).replace(
        '"discount_percent": null', '"discount_percent": 10.1234567890123456789',
    )
    with respx.mock() as router:
        route = router.get(f"{BASE}/discounts").respond(200, content=body.encode())
        async with LoyverseAPIClient("hu009-synthetic-token") as client:
            snapshot = await fetch_discount_snapshot(client)
        assert snapshot.includes_deleted is True
        assert snapshot.fetched_at.utcoffset().total_seconds() == 0
        assert snapshot.records[0].loyverse_id == DISCOUNT_ID
        assert snapshot.records[0].value == Decimal("10.1234567890123456789")
        assert snapshot.records[0].is_deleted is True
        assert route.calls[0].request.url.params["show_deleted"] == "true"


@pytest.mark.asyncio
async def test_invalid_later_record_prevents_snapshot_delivery():
    records = [source_discount(discount_percent=10), source_discount(name="", discount_percent=10)]
    with respx.mock() as router:
        router.get(f"{BASE}/discounts").respond(200, json={"discounts": records})
        async with LoyverseAPIClient("hu009-synthetic-token") as client:
            with pytest.raises(DiscountValidationError):
                await fetch_discount_snapshot(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403])
async def test_permission_and_subscription_failures_do_not_retry_or_expose_secrets(status):
    secret = "PRIVATE_PROVIDER_PAYLOAD"
    sleep = AsyncMock()
    with respx.mock() as router:
        route = router.get(f"{BASE}/discounts").respond(status, text=secret)
        async with LoyverseAPIClient("hu009-synthetic-token", sleep=sleep) as client:
            with pytest.raises(LoyverseAPIError) as error:
                await fetch_discount_snapshot(client)
        assert error.value.status_code == status
        assert secret not in str(error.value) and "hu009-synthetic-token" not in str(error.value)
        assert route.call_count == 1
        sleep.assert_not_awaited()
        if status == 403:
            assert "descuentos" in str(error.value)


@pytest.mark.asyncio
async def test_transient_failure_reuses_bounded_retry_policy():
    sleep = AsyncMock()
    with respx.mock() as router:
        route = router.get(f"{BASE}/discounts").mock(side_effect=[
            httpx.Response(503), httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"discounts": []}),
        ])
        async with LoyverseAPIClient("hu009-synthetic-token", sleep=sleep) as client:
            snapshot = await fetch_discount_snapshot(client)
        assert snapshot.records == ()
        assert route.call_count == 3
        assert [call.args[0] for call in sleep.await_args_list] == [1, 2]


@pytest.mark.asyncio
async def test_malformed_json_is_not_a_valid_snapshot():
    with respx.mock() as router:
        router.get(f"{BASE}/discounts").respond(200, text="PRIVATE_NON_JSON")
        async with LoyverseAPIClient("hu009-synthetic-token") as client:
            with pytest.raises(LoyverseResponseError) as error:
                await fetch_discount_snapshot(client)
        assert "PRIVATE_NON_JSON" not in str(error.value)

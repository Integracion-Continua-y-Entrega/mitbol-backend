"""Pruebas HTTP con RESPX; no utilizan el token real ni acceso a PostgreSQL."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import format_datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services.loyverse_client import (
    LoyverseAPIClient, LoyverseAPIError, LoyverseConfigurationError,
    LoyverseResponseError, LoyverseTransientError, RateLimitException,
    _retry_after_seconds,
)

BASE = LoyverseAPIClient.BASE_URL


@pytest.mark.asyncio
async def test_pagination_keeps_filters_and_closes_connection():
    with respx.mock() as router:
        route = router.get(f"{BASE}/items").mock(side_effect=[
            httpx.Response(200, json={"items": [{"id": "one"}], "cursor": "page-two"}),
            httpx.Response(200, json={"items": [{"id": "two"}]}),
        ])
        async with LoyverseAPIClient("test-token", page_size=1) as client:
            result = await client.get_all_paginated("/items", "items", {"show_deleted": "true"})
        assert result == [{"id": "one"}, {"id": "two"}]
        assert route.calls[0].request.url.params["limit"] == "1"
        assert "cursor" not in route.calls[0].request.url.params
        assert route.calls[1].request.url.params["cursor"] == "page-two"
        assert route.calls[1].request.url.params["show_deleted"] == "true"
        assert route.calls[0].request.headers["Authorization"] == "Bearer test-token"
        assert client.page_counts == {"/items": 2}
        assert client._http.is_closed


@pytest.mark.asyncio
async def test_stores_are_read_without_undocumented_pagination():
    with respx.mock() as router:
        route = router.get(f"{BASE}/stores").respond(200, json={"stores": []})
        async with LoyverseAPIClient("test-token") as client:
            assert await client.get_stores() == []
        assert not route.calls[0].request.url.params


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"items": None}, {"items": {}}, {"items": [1]}])
async def test_missing_or_invalid_collection_is_not_an_empty_catalog(payload):
    with respx.mock() as router:
        route = router.get(f"{BASE}/items").respond(200, json=payload)
        async with LoyverseAPIClient("test-token") as client:
            with pytest.raises(LoyverseResponseError):
                await client.get_items()
        assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("second_page", [
    {"items": [{"id": "two"}], "cursor": "again"},
    {"items": [], "cursor": "another"},
    {"items": [{"id": "two"}], "cursor": 17},
    {},
])
async def test_broken_second_page_never_returns_partial_results(second_page):
    with respx.mock() as router:
        router.get(f"{BASE}/items").mock(side_effect=[
            httpx.Response(200, json={"items": [{"id": "one"}], "cursor": "again"}),
            httpx.Response(200, json=second_page),
        ])
        async with LoyverseAPIClient("test-token") as client:
            with pytest.raises(LoyverseResponseError):
                await client.get_items()


@pytest.mark.asyncio
async def test_decimal_json_preserves_original_precision_and_zero():
    with respx.mock() as router:
        router.get(f"{BASE}/items").respond(
            200, content=b'{"items":[{"price":0.10000000000000001},{"price":0},{"price":null}]}',
        )
        async with LoyverseAPIClient("test-token") as client:
            records = await client.get_items()
        assert records[0]["price"] == Decimal("0.10000000000000001")
        assert records[1]["price"] == 0
        assert records[2]["price"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not-json", b"[]", b'{"price":NaN}'])
async def test_invalid_json_is_rejected(content):
    with respx.mock() as router:
        route = router.get(f"{BASE}/items").respond(200, content=content)
        async with LoyverseAPIClient("test-token") as client:
            with pytest.raises(LoyverseResponseError):
                await client.get_items()
        assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 302])
async def test_permanent_errors_and_redirects_are_not_retried_or_leaked(status):
    with respx.mock() as router:
        route = router.get(f"{BASE}/stores").respond(
            status, json={"detail": "server-body-test-token"},
            headers={"Location": "https://another-host.invalid"},
        )
        async with LoyverseAPIClient("test-token", sleep=AsyncMock()) as client:
            with pytest.raises(LoyverseAPIError) as caught:
                await client.get_stores()
        assert caught.value.status_code == status
        assert "test-token" not in str(caught.value)
        assert route.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 503])
async def test_transient_http_error_is_retried(status):
    sleep = AsyncMock()
    with respx.mock() as router:
        route = router.get(f"{BASE}/items").mock(side_effect=[
            httpx.Response(status), httpx.Response(200, json={"items": []}),
        ])
        async with LoyverseAPIClient("test-token", sleep=sleep) as client:
            assert await client.get_items() == []
        assert route.call_count == 2
        sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_retry_after_is_respected():
    sleep = AsyncMock()
    with respx.mock() as router:
        router.get(f"{BASE}/modifiers").mock(side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"modifiers": []}),
        ])
        async with LoyverseAPIClient("test-token", sleep=sleep) as client:
            assert await client.get_modifiers() == []
        sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_retry_after_above_limit_does_not_retry_earlier():
    sleep = AsyncMock()
    with respx.mock() as router:
        route = router.get(f"{BASE}/items").respond(429, headers={"Retry-After": "301"})
        async with LoyverseAPIClient("test-token", sleep=sleep) as client:
            with pytest.raises(LoyverseAPIError, match="consulta pospuesta"):
                await client.get_items()
        assert route.call_count == 1
        sleep.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
async def test_connection_errors_stop_after_bounded_attempts(failure):
    sleep = AsyncMock()
    with respx.mock() as router:
        route = router.get(f"{BASE}/items").mock(side_effect=failure("unsafe-test-token"))
        async with LoyverseAPIClient("test-token", sleep=sleep) as client:
            with pytest.raises(LoyverseTransientError) as caught:
                await client.get_items()
        assert "test-token" not in str(caught.value)
        assert route.call_count == 3
        assert [call.args[0] for call in sleep.await_args_list] == [1, 2]


@pytest.mark.asyncio
async def test_exhausted_rate_limit_preserves_specific_error():
    with respx.mock() as router:
        route = router.get(f"{BASE}/items").respond(429)
        async with LoyverseAPIClient("test-token", sleep=AsyncMock()) as client:
            with pytest.raises(RateLimitException):
                await client.get_items()
        assert route.call_count == 3


@pytest.mark.asyncio
async def test_token_cannot_be_sent_to_an_external_endpoint():
    async with LoyverseAPIClient("test-token") as client:
        with pytest.raises(ValueError):
            await client.get("https://another-host.invalid/items")
        assert client.request_count == 0


def test_empty_token_is_rejected():
    with pytest.raises(LoyverseConfigurationError):
        LoyverseAPIClient("")


def test_retry_after_http_date_and_invalid_headers():
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10), usegmt=True)
    delay = _retry_after_seconds(date)
    assert 0 <= delay <= 10
    assert _retry_after_seconds("invalid") is None
    assert _retry_after_seconds("NaN") is None
    assert _retry_after_seconds("-5") == 0

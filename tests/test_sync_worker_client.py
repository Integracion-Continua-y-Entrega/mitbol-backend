"""Delegación autenticada a un endpoint fijo, sin tráfico real."""

import httpx
import json
from pydantic import SecretStr
import pytest

from app.services.sync_worker_client import SyncWorkerClient, validate_worker_url
from app.services.manual_sync_coordinator import ManualSyncUnavailableError, SyncDestinationMismatchError
from app.services.catalog_errors import CatalogSyncBusyError
from app.schemas.sync_destination import SyncDestination


TOKEN = "synthetic-worker-token-" + "x" * 32
DESTINATION = SyncDestination(environment="test", host="127.0.0.1", port=5432,
    database="mitbol_auth_local", store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12")


@pytest.mark.asyncio
async def test_fixed_target_body_and_auth_header():
    def respond(request):
        assert str(request.url) == "http://127.0.0.1:8766/internal/sync/execute"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert json.loads(request.content) == {"target": "discounts", "destination": DESTINATION.model_dump()}
        return httpx.Response(200, json=dict(target="discounts", destination=DESTINATION.model_dump(), status="completed", result={"discounts_processed": 3}))
    client = SyncWorkerClient("http://127.0.0.1:8766", SecretStr(TOKEN), destination=DESTINATION, transport=httpx.MockTransport(respond))
    assert await client.execute("discounts") == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 401, 403, 500, 503])
async def test_failure_never_retries_or_exposes_worker_body(status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "http://remote.invalid/"}, text="private-password")
    client = SyncWorkerClient("http://localhost:8766", SecretStr(TOKEN), destination=DESTINATION, transport=httpx.MockTransport(respond))
    with pytest.raises(ManualSyncUnavailableError) as error:
        await client.execute("catalog")
    assert len(calls) == 1 and "private-password" not in str(error.value)


@pytest.mark.asyncio
async def test_busy_maps_to_domain_conflict():
    client = SyncWorkerClient("http://localhost:8766", SecretStr(TOKEN), destination=DESTINATION, transport=httpx.MockTransport(lambda r: httpx.Response(409)))
    with pytest.raises(CatalogSyncBusyError):
        await client.execute("discounts")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["rejected", "missing", "wrong_store", "wrong_database"])
async def test_unbound_response_never_confirms_completion(fault):
    calls = []
    def respond(request):
        calls.append(request)
        destination = DESTINATION.model_dump()
        if fault == "rejected":
            return httpx.Response(412, text="private-password")
        if fault == "wrong_store":
            destination["store_id"] = "497f6eca-6276-4993-bfeb-53cbbbba6f08"
        if fault == "wrong_database":
            destination["database"] = "other_database"
        result = dict(target="discounts", status="completed", result={"discounts_processed": 3})
        if fault != "missing":
            result["destination"] = destination
        return httpx.Response(200, json=result)
    client = SyncWorkerClient("http://localhost:8766", SecretStr(TOKEN), destination=DESTINATION,
                              transport=httpx.MockTransport(respond))
    error_type = ManualSyncUnavailableError if fault == "missing" else SyncDestinationMismatchError
    with pytest.raises(error_type) as error:
        await client.execute("discounts")
    assert len(calls) == 1 and "private-password" not in str(error.value)


@pytest.mark.asyncio
async def test_invalid_result_is_not_announced_as_completed():
    client = SyncWorkerClient("http://localhost:8766", SecretStr(TOKEN), destination=DESTINATION, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"target": "discounts", "result": {"discounts_processed": True}})))
    with pytest.raises(ManualSyncUnavailableError):
        await client.execute("discounts")


@pytest.mark.parametrize("url", ["http://remote.invalid", "http://127.0.0.1:1/path", "http://user:password@localhost", "http://localhost/?secret=x", "http://localhost/#x", "file:///tmp/x"])
def test_remote_or_ambiguous_worker_url_is_rejected(url):
    with pytest.raises(ValueError):
        validate_worker_url(url)

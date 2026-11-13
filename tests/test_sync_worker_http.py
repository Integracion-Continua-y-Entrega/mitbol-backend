"""Worker privado con runtime simulado; nunca consulta al proveedor."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from app.sync_worker import create_worker
from app.schemas.sync_requests import completed_response
from app.services.catalog_errors import CatalogSyncBusyError
from app.schemas.sync_destination import SyncDestination
from app.services.sync_worker_runtime import SyncWorkerRuntime


TOKEN = "synthetic-worker-token-" + "x" * 32
DESTINATION = SyncDestination(environment="test", host="127.0.0.1", port=5432,
    database="mitbol_auth_local", store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12")


def body():
    return {"target": "discounts", "destination": DESTINATION.model_dump()}


@pytest.fixture
def backend():
    runtime = SimpleNamespace(config=SimpleNamespace(worker_token=SecretStr(TOKEN), destination=DESTINATION),
        execute=AsyncMock(return_value=completed_response("discounts", 1)), close=Mock())
    runtime.require_destination = lambda value: SyncWorkerRuntime.require_destination(runtime, value)
    with TestClient(create_worker(runtime=runtime), base_url="http://127.0.0.1:8766", client=("127.0.0.1", 12345)) as client:
        yield SimpleNamespace(runtime=runtime, client=client, headers={"Authorization": "Bearer " + TOKEN})
    runtime.close.assert_called_once()


def test_authenticated_request_waits_for_completed_result(backend):
    response = backend.client.post("/internal/sync/execute", json=body(), headers=backend.headers)
    assert response.status_code == 200
    assert response.json()["result"] == {"discounts_processed": 1}
    backend.runtime.execute.assert_awaited_once_with("discounts")
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["destination"] == DESTINATION.model_dump()


@pytest.mark.parametrize("field,value", [
    ("environment", "local"), ("host", "localhost"), ("port", 5433),
    ("database", "mitbol_dev"), ("store_id", "497f6eca-6276-4993-bfeb-53cbbbba6f08"),
])
def test_destination_mismatch_is_rejected_before_execution(backend, field, value):
    payload = body()
    payload["destination"][field] = value
    response = backend.client.post("/internal/sync/execute", json=payload, headers=backend.headers)
    assert response.status_code == 412
    backend.runtime.execute.assert_not_called()


def test_old_protocol_without_destination_cannot_execute(backend):
    response = backend.client.post("/internal/sync/execute", json={"target": "discounts"}, headers=backend.headers)
    assert response.status_code == 422
    backend.runtime.execute.assert_not_called()


def test_missing_token_is_rejected_before_execution(backend):
    assert backend.client.post("/internal/sync/execute", json={"target": "discounts"}).status_code == 401
    backend.runtime.execute.assert_not_called()


@pytest.mark.parametrize("headers", [{"Origin": "http://localhost"}, {"X-Forwarded-For": "127.0.0.1"}])
def test_browser_and_proxy_requests_are_rejected(backend, headers):
    assert backend.client.post("/internal/sync/execute", json={"target": "discounts"}, headers=backend.headers | headers).status_code == 403
    backend.runtime.execute.assert_not_called()


def test_invalid_body_does_not_echo_configuration(backend):
    response = backend.client.post("/internal/sync/execute", json={"target": "all", "token": "private-password"}, headers=backend.headers)
    assert response.status_code == 422 and "private-password" not in response.text
    backend.runtime.execute.assert_not_called()


def test_body_is_bounded(backend):
    response = backend.client.post("/internal/sync/execute", content="x" * 2049, headers=backend.headers)
    assert response.status_code == 413
    backend.runtime.execute.assert_not_called()


@pytest.mark.parametrize("error,status", [(CatalogSyncBusyError("synthetic"), 409), (RuntimeError("SQL private-password"), 503)])
def test_worker_errors_are_safe(backend, error, status):
    backend.runtime.execute.side_effect = error
    response = backend.client.post("/internal/sync/execute", json=body(), headers=backend.headers)
    assert response.status_code == status and "private-password" not in response.text


def test_remote_peer_is_rejected_even_with_token():
    runtime = SimpleNamespace(config=SimpleNamespace(worker_token=SecretStr(TOKEN)), execute=AsyncMock(), close=Mock())
    with TestClient(create_worker(runtime=runtime), base_url="http://127.0.0.1:8766", client=("203.0.113.2", 123)) as client:
        assert client.post("/internal/sync/execute", json={"target": "discounts"}, headers={"Authorization": "Bearer " + TOKEN}).status_code == 403
    runtime.execute.assert_not_called()

"""Contrato HTTP y permisos con identidad y servicio simulados."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.api import admin_sync_status as routes
from app.api.auth_dependencies import get_authenticated_database, get_current_user
from app.core.auth_errors import AuthError
from app.schemas.sync_status import EntitySyncSummary, SyncEntity
from app.services.enrichment_errors import EnrichmentError


@pytest.fixture
def backend(monkeypatch):
    app = FastAPI()
    app.include_router(routes.create_sync_status_router(authenticated=True))
    state = SimpleNamespace(permissions={"catalog:read"}, db=Mock(info={"auth_context": object()}))

    def principal():
        if state.permissions is None:
            raise AuthError(401, "Sesión requerida")
        return SimpleNamespace(permissions=state.permissions)

    app.dependency_overrides[get_current_user] = principal
    app.dependency_overrides[get_authenticated_database] = lambda: state.db

    async def handle(request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    app.add_exception_handler(AuthError, handle)
    app.add_exception_handler(EnrichmentError, handle)
    state.read = Mock(return_value=[EntitySyncSummary(
        entity=entity, total_count=0, synced_count=0, last_synced_at=None,
    ) for entity in SyncEntity])
    monkeypatch.setattr(routes, "get_sync_summaries", state.read)
    state.active = Mock(return_value=False)
    monkeypatch.setattr(routes, "sync_execution_is_active", state.active)
    with TestClient(app) as state.client:
        yield state


def test_get_returns_observations_without_claiming_execution_history(backend):
    response = backend.client.get("/api/admin/sync/status")
    assert response.status_code == 200
    assert response.json()["history_available"] is False
    assert response.json()["execution_active"] is False
    assert len(response.json()["entities"]) == 5
    assert all(row["last_synced_at"] is None for row in response.json()["entities"])
    backend.read.assert_called_once_with(backend.db)
    backend.db.commit.assert_not_called()


def test_active_execution_does_not_claim_an_execution_history(backend):
    backend.active.return_value = True
    response = backend.client.get("/api/admin/sync/status")
    assert response.status_code == 200
    assert response.json()["execution_active"] is True
    assert response.json()["history_available"] is False


@pytest.mark.parametrize("permissions,status", [(None, 401), (set(), 403)])
def test_access_denied_before_read(backend, permissions, status):
    backend.permissions = permissions
    assert backend.client.get("/api/admin/sync/status").status_code == status
    backend.read.assert_not_called()


@pytest.mark.parametrize("error,status", [
    (EnrichmentError(503, "Esquema no disponible"), 503),
    (RuntimeError("SQL private-password"), 500),
])
def test_errors_are_safe_and_not_presented_as_empty_state(backend, error, status):
    backend.read.side_effect = error
    response = backend.client.get("/api/admin/sync/status")
    assert response.status_code == status
    assert "entities" not in response.json()
    assert "private-password" not in response.text


def test_openapi_exposes_get_without_session_parameter(backend):
    path = backend.client.get("/openapi.json").json()["paths"]["/api/admin/sync/status"]
    assert set(path) == {"get"}
    assert path["get"].get("parameters", []) == []

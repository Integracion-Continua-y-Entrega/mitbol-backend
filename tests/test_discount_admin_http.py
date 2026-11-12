"""Router aislado con permisos reales y dependencias de sesión simuladas."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, Table

from app.api import admin_discounts as routes
from app.api.auth_dependencies import get_authenticated_database, get_current_user
from app.core.auth_errors import AuthError
from app.schemas.discounts import DiscountRead
from app.services.enrichment_errors import EnrichmentError


@pytest.fixture
def backend(monkeypatch):
    app = FastAPI()
    table = Table("Discounts", MetaData())
    app.include_router(routes.create_discount_router(table, authenticated=True))
    state = SimpleNamespace(permissions={"catalog:read"}, db=Mock(info={"auth_context": object()}))

    def principal():
        if state.permissions is None:
            raise AuthError(401, "Sesión requerida")
        return SimpleNamespace(permissions=state.permissions)

    app.dependency_overrides[get_current_user] = principal
    app.dependency_overrides[get_authenticated_database] = lambda: state.db

    async def error_response(request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    app.add_exception_handler(AuthError, error_response)
    app.add_exception_handler(EnrichmentError, error_response)
    state.read = Mock(return_value=[])
    state.table = table
    monkeypatch.setattr(routes, "list_discounts", state.read)
    with TestClient(app) as state.client:
        yield state


def test_empty_list_and_explicit_pagination(backend):
    response = backend.client.get("/api/admin/discounts?limit=20&offset=40")
    assert response.status_code == 200 and response.json() == []
    backend.read.assert_called_once_with(db=backend.db, table=backend.table, limit=20, offset=40)
    backend.db.commit.assert_not_called()


def test_response_preserves_points_type_and_exact_decimal(backend):
    values = dict(discount_id=1, loyverse_id="synthetic", name="Consulta", scope="unknown",
                  is_active=False, synced_at=None, created_at=datetime(2026, 11, 12, tzinfo=timezone.utc))
    backend.read.return_value = [
        DiscountRead(**values, discount_type="FIXED_AMOUNT", value=Decimal("35.1234567890123456789")),
        DiscountRead(**dict(values, discount_id=2), discount_type="DISCOUNT_BY_POINTS", value=None),
    ]
    response = backend.client.get("/api/admin/discounts")
    assert response.status_code == 200
    assert response.json()[0]["value"] == "35.1234567890123456789"
    assert response.json()[0]["is_active"] is False
    assert response.json()[1]["discount_type"] == "DISCOUNT_BY_POINTS"
    assert response.json()[1]["value"] is None


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=x", "offset=-1", "offset=x"])
def test_invalid_pagination_does_not_call_service(backend, query):
    assert backend.client.get("/api/admin/discounts?" + query).status_code == 422
    backend.read.assert_not_called()


@pytest.mark.parametrize("permissions,expected", [(None, 401), (set(), 403)])
def test_access_denied_before_read(backend, permissions, expected):
    backend.permissions = permissions
    assert backend.client.get("/api/admin/discounts").status_code == expected
    backend.read.assert_not_called()


def test_database_error_does_not_expose_details(backend):
    backend.read.side_effect = RuntimeError("SQL secret-password")
    response = backend.client.get("/api/admin/discounts")
    assert response.status_code == 500
    assert "secret-password" not in response.text


def test_openapi_exposes_only_get_and_query_parameters(backend):
    operation = backend.client.get("/openapi.json").json()["paths"]["/api/admin/discounts"]
    assert set(operation) == {"get"}
    assert {p["name"] for p in operation["get"]["parameters"]} == {"limit", "offset"}

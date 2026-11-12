"""HU-006: contrato HTTP con identidad y servicios simulados, sin PostgreSQL.

No sustituye pruebas de login, CSRF, permisos SQL ni persistencia reales.
"""

from types import SimpleNamespace
import json
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from app.api.admin_catalog import create_admin_router
from app.api.auth_dependencies import get_authenticated_database, get_current_user
from app.core.auth_errors import AuthError
from app.core.permissions import permissions_for_roles
from app.schemas.highlights import ProductHighlightsResponse
from app.services import product_highlights_service as highlights
from app.services.enrichment_errors import EnrichmentError


@pytest.fixture
def http_backend(monkeypatch):
    app = FastAPI()
    app.include_router(create_admin_router(authenticated=True))

    async def error_response(request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": "Error de prueba"})

    app.add_exception_handler(AuthError, error_response)
    app.add_exception_handler(EnrichmentError, error_response)
    context = SimpleNamespace(principal=SimpleNamespace(account_id=11), request_id=uuid4())
    b = SimpleNamespace(role="admin", db=Mock(info={"auth_context": context}), app=app)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        permissions=permissions_for_roles([b.role], employee_active=True),
    )
    app.dependency_overrides[get_authenticated_database] = lambda: b.db
    b.read = Mock(return_value=ProductHighlightsResponse(product_id=3, is_featured=True, is_new=False))
    b.write = Mock(side_effect=lambda **values: ProductHighlightsResponse(
        product_id=values["product_id"], is_featured=values["is_featured"], is_new=values["is_new"],
    ))
    monkeypatch.setattr(highlights, "get_product_highlights", b.read)
    monkeypatch.setattr(highlights, "update_product_highlights", b.write)
    with TestClient(app) as b.client:
        yield b


def test_staff_can_read_highlights_with_authenticated_session(http_backend):
    b = http_backend
    b.role = "staff"
    response = b.client.get("/api/admin/products/3/highlights")
    assert response.status_code == 200
    assert response.json() == {"product_id": 3, "is_featured": True, "is_new": False}
    b.read.assert_called_once_with(db=b.db, product_id=3)
    b.db.add.assert_not_called()


@pytest.mark.parametrize("featured,new", [(False, False), (False, True), (True, False), (True, True)])
def test_admin_updates_highlights_and_adds_audit(http_backend, featured, new):
    b = http_backend
    response = b.client.put("/api/admin/products/3/highlights", json={"is_featured": featured, "is_new": new})
    assert response.status_code == 200
    assert response.json() == {"product_id": 3, "is_featured": featured, "is_new": new}
    b.write.assert_called_once_with(db=b.db, product_id=3, is_featured=featured, is_new=new)
    event = b.db.add.call_args.args[0]
    assert (event.action, event.resource_type, event.resource_id) == ("catalog.product.highlights", "product", "3")
    assert event.actor_account_id == 11
    b.db.commit.assert_not_called()


def test_staff_cannot_edit_highlights(http_backend):
    b = http_backend
    b.role = "staff"
    response = b.client.put("/api/admin/products/3/highlights", json={"is_featured": True, "is_new": False})
    assert response.status_code == 403
    b.write.assert_not_called()
    b.db.add.assert_not_called()


@pytest.mark.parametrize("payload", [
    {}, {"is_featured": True},
    {"is_featured": "true", "is_new": False},
    {"is_featured": True, "is_new": 0},
    {"is_featured": None, "is_new": False},
    {"is_featured": True, "is_new": False, "is_active": True},
])
def test_invalid_payload_never_calls_service(http_backend, payload):
    b = http_backend
    assert b.client.put("/api/admin/products/3/highlights", json=payload).status_code == 422
    b.write.assert_not_called()
    b.db.add.assert_not_called()


def test_failed_edit_never_adds_success_audit(http_backend):
    b = http_backend
    b.write.side_effect = EnrichmentError(503, "Base no disponible")
    assert b.client.put("/api/admin/products/3/highlights", json={"is_featured": True, "is_new": False}).status_code == 503
    b.db.add.assert_not_called()


@pytest.mark.parametrize("authenticated", [False, True])
def test_router_builds_and_documents_highlights_without_database_query(authenticated):
    app = FastAPI()
    app.include_router(create_admin_router(authenticated=authenticated))
    schema = app.openapi()
    operations = schema["paths"]["/api/admin/products/{product_id}/highlights"]
    assert {"get", "put"} <= set(operations)
    for method in ["get", "put"]:
        assert all(p["name"] != "db" for p in operations[method].get("parameters", []))
    assert schema["components"]["schemas"]["ProductHighlightsUpdate"]["additionalProperties"] is False


def test_local_and_cloud_writer_profiles_share_exact_column_policy():
    root = Path(__file__).resolve().parents[1] / "docs/database"
    policies = []
    for filename in ["verify_admin_writer_local.sql", "verify_admin_writer_neon.sql"]:
        source = (root / filename).read_text(encoding="utf-8")
        policy = source.split("policy CONSTANT jsonb := '", 1)[1].split("';", 1)[0]
        policies.append(json.loads(policy))
    assert policies[0] == policies[1]
    assert set(policies[0]["Products"]["UPDATE"]) == {"description", "is_available", "is_featured", "is_new"}

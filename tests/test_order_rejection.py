"""HU-026: motivo obligatorio, rechazo atómico y seguimiento sin notas internas."""

from unittest.mock import Mock
from uuid import UUID, uuid4

from pydantic import ValidationError
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.models import Order, OrderStatusHistory
from app.schemas.orders import OrderRejectionInput
from app.services import staff_order_service
from test_auth_api import csrf_headers, login
from test_staff_order_management import (
    backend, coupon_backend, auth_backend, auth_database, password_hash, guest_order, decide,
)


@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 501, "No\nhay", "No\x00hay", 123])
def test_service_rejects_invalid_reason_before_querying_or_mutating(reason):
    db = Mock()
    with pytest.raises(AuthError) as error:
        staff_order_service.decide_order(db, uuid4(), accepted=False, actor_account_id=1, reason=reason)
    assert error.value.status_code == 422 and db.mock_calls == []


@pytest.mark.parametrize("reason", ["A", "x" * 500, "  Sin disponibilidad 🍔  "])
def test_reason_contract_accepts_boundaries_and_normalizes_outer_spaces(reason):
    assert OrderRejectionInput(reason=reason).reason == reason.strip()
    with pytest.raises(ValidationError):
        OrderRejectionInput()


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_rejection_persists_reason_actor_date_and_keeps_guest_tracking_private(backend, role):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b, role))
    response = decide(b, receipt, headers, "reject", {"reason": "  Capacidad operativa insuficiente  "})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "Rechazado" and result["items"] == receipt["items"]
    assert result["total"] == receipt["total"] and result["needs_attention"] is False
    last = result["history"][-1]
    assert last["reason"] == "Capacidad operativa insuficiente" and last["created_at"]
    assert last["actor_account_id"] == b.ids[role][0] and last["previous_status"] == "Solicitud recibida"
    assert decide(b, receipt, headers, "reject", {"reason": "Otro motivo"}).status_code == 409
    with b.sessions() as db:
        events = db.scalars(select(OrderStatusHistory).where(
            OrderStatusHistory.order_id == UUID(receipt["order_id"]))).all()
        assert len(events) == 2
        assert next(row for row in events if row.status == "Rechazado").reason == "Capacidad operativa insuficiente"
    b.client.cookies.clear()
    tracked = b.client.get(receipt["tracking"]["path"], headers={"Authorization": "Bearer " + receipt["tracking"]["token"]})
    assert tracked.status_code == 200 and tracked.json()["status"] == "Rechazado"
    assert "Capacidad operativa insuficiente" not in tracked.text and "actor_account_id" not in tracked.text


def test_customer_sees_rejected_state_without_operational_reason(backend):
    b = backend
    customer = csrf_headers(login(b, "customer"))
    created = b.client.post("/api/orders", headers=customer | {"Idempotency-Key": str(uuid4())},
        json={"cart": b.selection["cart"], "data": {"modality": "dine_in", "name": "Cliente sintético"}})
    assert created.status_code == 201, created.text
    headers = csrf_headers(login(b, "staff"))
    assert decide(b, created.json(), headers, "reject", {"reason": "Nota operativa interna"}).status_code == 200
    login(b, "customer")
    response = b.client.get(f'/api/auth/orders/{created.json()["order_id"]}')
    assert response.status_code == 200 and response.json()["status"] == "Rechazado"
    assert response.json()["can_cancel"] is False
    assert "Nota operativa interna" not in response.text and "actor_account_id" not in response.text


def test_history_failure_rolls_back_rejected_state_and_reason(backend):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b, "staff"))
    engine = b.service.sessions.kw["bind"]
    def fail(conn, cursor, statement, parameters, context, many):
        """Falla el INSERT posterior al cambio de estado para verificar su rollback conjunto."""
        if 'INSERT INTO "OrderStatusHistories"' in statement:
            raise OperationalError("synthetic", {}, Exception("private-password"))
    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = decide(b, receipt, headers, "reject", {"reason": "No hay disponibilidad"})
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert response.status_code == 503 and "private-password" not in response.text
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == "Solicitud recibida"
        assert len(db.scalars(select(OrderStatusHistory)).all()) == 1
    assert decide(b, receipt, headers, "reject", {"reason": "No hay disponibilidad"}).status_code == 200


def test_rejecting_unknown_order_with_valid_reason_returns_404(backend):
    headers = csrf_headers(login(backend, "staff"))
    assert decide(backend, {"order_id": str(uuid4())}, headers, "reject", {"reason": "Sin disponibilidad"}).status_code == 404

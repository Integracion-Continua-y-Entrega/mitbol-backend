"""HU-024: prioridad y espera informativa en PostgreSQL temporal, sin cambios de estado."""

from datetime import timedelta
from uuid import UUID

from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models import Order, OrderStatusHistory
from test_auth_api import login
from test_staff_order_management import (
    backend, coupon_backend, auth_backend, auth_database, password_hash, guest_order,
)


PATH = "/api/admin/orders"


def set_order(b, receipt, *, age, status="Solicitud recibida"):
    """Prepara fechas y estados sintéticos para comprobar el orden de la cola."""
    with b.sessions.begin() as db:
        row = db.get(Order, UUID(receipt["order_id"]))
        row.created_at = b.now.value - age
        row.status = status


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_pending_orders_precede_older_attended_orders_before_pagination(backend, role):
    b = backend
    attended, older_pending, newer_pending = [guest_order(b) for _ in range(3)]
    set_order(b, attended, age=timedelta(days=2), status="Aceptado")
    set_order(b, older_pending, age=timedelta(minutes=20))
    set_order(b, newer_pending, age=timedelta(minutes=5))
    login(b, role)
    pages = [b.client.get(PATH, params={"limit": 1, "offset": offset}) for offset in range(3)]
    assert all(response.status_code == 200 for response in pages)
    assert all(response.json()["total"] == 3 for response in pages)
    assert [response.json()["items"][0]["order_id"] for response in pages] == [
        older_pending["order_id"], newer_pending["order_id"], attended["order_id"],
    ]
    assert pages[0].json()["items"][0]["needs_attention"] is True
    assert pages[1].json()["items"][0]["needs_attention"] is False
    assert pages[2].json()["items"][0]["pending_wait_seconds"] is None
    assert pages[2].json()["items"][0]["needs_attention"] is False
    filtered = b.client.get(PATH, params={"status": "Aceptado"}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["order_id"] == attended["order_id"]


def test_equal_dates_use_uuid_tie_break_across_pages(backend):
    b = backend
    receipts = [guest_order(b) for _ in range(3)]
    for receipt in receipts:
        set_order(b, receipt, age=timedelta(minutes=3))
    login(b, "staff")
    actual = [b.client.get(PATH, params={"limit": 1, "offset": offset}).json()["items"][0]["order_id"]
              for offset in range(3)]
    assert actual == [str(value) for value in sorted(UUID(row["order_id"]) for row in receipts)]
    assert b.client.get(PATH, params={"offset": 3}).json()["items"] == []


@pytest.mark.parametrize("age,seconds,attention", [
    (timedelta(seconds=-20), 0, False), (timedelta(0), 0, False),
    (timedelta(seconds=599, microseconds=999999), 599, False),
    (timedelta(seconds=600), 600, True), (timedelta(seconds=601), 601, True),
    (timedelta(days=2), 172800, True),
])
def test_wait_and_warning_boundary_match_between_list_and_detail(backend, age, seconds, attention):
    b = backend
    receipt = guest_order(b)
    set_order(b, receipt, age=age)
    login(b, "staff")
    listing = b.client.get(PATH)
    detail = b.client.get(f'{PATH}/{receipt["order_id"]}')
    assert listing.status_code == detail.status_code == 200
    for body in (listing.json()["items"][0], detail.json()):
        assert body["pending_wait_seconds"] == seconds
        assert body["needs_attention"] is attention
    assert listing.headers["cache-control"] == detail.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("state", ["Aceptado", "En preparación", "Listo para recoger", "Servido",
                                   "Entregado", "Completado", "Rechazado", "Cancelado"])
def test_attended_orders_do_not_keep_pending_warning(backend, state):
    b = backend
    receipt = guest_order(b)
    set_order(b, receipt, age=timedelta(days=3), status=state)
    login(b, "admin")
    for body in (b.client.get(PATH).json()["items"][0], b.client.get(f'{PATH}/{receipt["order_id"]}').json()):
        assert body["pending_wait_seconds"] is None and body["needs_attention"] is False


def test_configured_threshold_changes_warning_without_changing_order(backend):
    b = backend
    receipt = guest_order(b)
    set_order(b, receipt, age=timedelta(seconds=45))
    login(b, "staff")
    for threshold, expected in ((30, True), (60, False)):
        b.app.state.settings = b.config.model_copy(update={"order_pending_warning_seconds": threshold})
        for body in (b.client.get(PATH).json()["items"][0], b.client.get(f'{PATH}/{receipt["order_id"]}').json()):
            assert body["pending_wait_seconds"] == 45 and body["needs_attention"] is expected
    with b.sessions() as db:
        assert db.get(Order, UUID(receipt["order_id"])).status == "Solicitud recibida"


def test_repeated_polling_never_cancels_or_attends_old_requests(backend):
    b = backend
    receipt = guest_order(b)
    set_order(b, receipt, age=timedelta(days=90))
    login(b, "staff")
    for _ in range(3):
        assert b.client.get(PATH).json()["items"][0]["needs_attention"] is True
        assert b.client.get(f'{PATH}/{receipt["order_id"]}').json()["status"] == "Solicitud recibida"
    with b.sessions() as db:
        order = db.get(Order, UUID(receipt["order_id"]))
        assert order.status == "Solicitud recibida"
        assert [row.status for row in db.scalars(select(OrderStatusHistory)).all()] == ["Solicitud recibida"]


@pytest.mark.parametrize("value", [0, -1, 86401, "invalid", 2.5, None])
def test_invalid_warning_configuration_is_rejected(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
                 auth_enabled=False, admin_enabled=False, order_pending_warning_seconds=value)


def test_warning_setting_can_be_configured_from_process_environment(monkeypatch):
    monkeypatch.setenv("ORDER_PENDING_WARNING_SECONDS", "42")
    config = Settings(_env_file=None, app_env="test", database_url="postgresql://unused@localhost/unused",
                      auth_enabled=False, admin_enabled=False)
    assert config.order_pending_warning_seconds == 42

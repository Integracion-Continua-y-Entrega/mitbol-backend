"""Consulta y decisiones reales en PostgreSQL desechable; sin servicios externos."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from app.core.auth_errors import AuthError
from app.main import create_app
from app.models import CustomerCoupon, Employee, Order, OrderStatusHistory, ProductVariant
from app.services import order_cancellation_service, staff_order_service
from test_auth_api import auth_database, backend as auth_backend, csrf_headers, login, ORIGIN, password_hash
from test_auth_persistence import config_for
from test_coupon_selection import backend as coupon_backend


PATH = "/api/admin/orders"


@pytest.fixture
def backend(coupon_backend):
    with coupon_backend.sessions.kw["bind"].begin() as db:
        command.upgrade(config_for(db),"20261121_0014")
    return coupon_backend


def guest_order(b):
    b.client.cookies.clear()
    response = b.client.post("/api/orders",headers={"Idempotency-Key":str(uuid4())},
        json={"cart":b.selection["cart"],"data":{"modality":"pickup","name":"Alias privado","phone":"+522281234567"}})
    assert response.status_code == 201, response.text
    return response.json()


def decide(b, receipt, headers, action="accept", body=None):
    """Usa un motivo válido por defecto al rechazar y conserva los cuerpos inválidos explícitos."""
    if body is None:
        body = {"reason": "Motivo sintético de rechazo"} if action == "reject" else {}
    return b.client.post(f'{PATH}/{receipt["order_id"]}/{action}',headers=headers,json=body)


@pytest.mark.parametrize("role", ["admin","staff"])
def test_list_filter_pagination_detail_and_private_data_boundaries(backend, role):
    b = backend
    orders = [guest_order(b) for _ in range(3)]
    cancelled = b.client.post(f'/api/orders/{orders[0]["order_id"]}/cancel',json={},
        headers={"Authorization":f'Bearer {orders[0]["tracking"]["token"]}'})
    assert cancelled.status_code == 200
    login(b,role)
    first = b.client.get(PATH,params={"status":"Solicitud recibida","limit":1})
    second = b.client.get(PATH,params={"status":"Solicitud recibida","limit":1,"offset":1})
    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == 2
    assert first.json()["items"][0]["order_id"] != second.json()["items"][0]["order_id"]
    assert b.client.get(PATH).json()["total"] == 3
    assert b.client.get(PATH,params={"offset":100}).json()["items"] == []
    detail = b.client.get(f'{PATH}/{orders[1]["order_id"]}')
    assert detail.status_code == 200 and detail.headers["cache-control"] == "no-store"
    assert detail.json()["items"] == orders[1]["items"] and detail.json()["total"] == "170.00"
    assert detail.json()["data"]["phone"] == "+522281234567"
    assert detail.json()["history"][0]["status"] == "Solicitud recibida"
    for response in (first,second,detail):
        assert "guest_tracking_token" not in response.text and "request_hash" not in response.text
        assert orders[1]["tracking"]["token"] not in response.text and "idempotency_key" not in response.text


@pytest.mark.parametrize("role", [None,"customer"])
def test_guests_and_customers_have_no_staff_permissions(backend, role):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b,role)) if role else {"Origin":ORIGIN}
    responses = [b.client.get(PATH),b.client.get(f'{PATH}/{receipt["order_id"]}'),
                 decide(b,receipt,headers),decide(b,receipt,headers,"reject",{"reason":"No autorizado"})]
    assert {response.status_code for response in responses} == {403 if role else 401}
    with b.sessions() as db:
        assert db.get(Order,UUID(receipt["order_id"])).status == "Solicitud recibida"
        assert len(db.scalars(select(OrderStatusHistory)).all()) == 1


def test_inactive_employee_and_missing_csrf_cannot_decide(backend):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b,"staff"))
    assert decide(b,receipt,{"Origin":ORIGIN}).status_code == 403
    assert decide(b,receipt,headers | {"Origin":"https://evil.invalid"}).status_code == 403
    with b.sessions.begin() as db:
        db.get(Employee,b.ids["staff"][1]).is_active = False
    assert b.client.get(PATH).status_code == 401
    assert decide(b,receipt,headers).status_code == 401


@pytest.mark.parametrize("role", ["admin","staff"])
@pytest.mark.parametrize("action,status", [("accept","Confirmado"),("reject","Rechazado")])
def test_decisions_keep_history_amounts_and_existing_tracking(backend, role, action, status):
    b = backend
    receipt = guest_order(b)
    if action == "reject":
        with b.sessions.begin() as db:
            db.get(ProductVariant,b.products[0].variants[0].product_variant_id).price = 999
    headers = csrf_headers(login(b,role))
    body = {"reason":"  No hay disponibilidad  "} if action == "reject" else {}
    response = decide(b,receipt,headers,action,body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == status and result["items"] == receipt["items"] and result["total"] == receipt["total"]
    assert [row["status"] for row in result["history"]] == ["Solicitud recibida",status]
    last = result["history"][-1]
    assert last["previous_status"] == "Solicitud recibida" and last["actor_account_id"] == b.ids[role][0]
    assert last["reason"] == ("No hay disponibilidad" if action == "reject" else None)
    assert decide(b,receipt,headers,action,body).status_code == 409
    assert decide(b,receipt,headers,"reject" if action == "accept" else "accept").status_code == 409
    with b.sessions.begin() as db:
        db.get(ProductVariant,b.products[0].variants[0].product_variant_id).price = 999
    b.client.cookies.clear()
    private = {"Authorization":f'Bearer {receipt["tracking"]["token"]}'}
    tracked = b.client.get(receipt["tracking"]["path"],headers=private)
    assert tracked.status_code == 200 and tracked.json()["status"] == status
    assert tracked.json()["total"] == receipt["total"] and "history" not in tracked.json()
    assert b.client.post(f'/api/orders/{receipt["order_id"]}/cancel',headers=private,json={}).status_code == 409


@pytest.mark.parametrize("status", ["Cancelado","Aceptado","Rechazado","En preparación"])
@pytest.mark.parametrize("action", ["accept","reject"])
def test_only_received_requests_can_be_decided(backend, status, action):
    b = backend
    receipt = guest_order(b)
    with b.sessions.begin() as db:
        db.get(Order,UUID(receipt["order_id"])).status = status
    headers = csrf_headers(login(b,"staff"))
    assert decide(b,receipt,headers,action).status_code == 409
    with b.sessions() as db:
        assert db.get(Order,UUID(receipt["order_id"])).status == status
        assert len(db.scalars(select(OrderStatusHistory)).all()) == 1


@pytest.mark.parametrize("body", [{}, {"reason":None}, {"reason":" "}, {"reason":"x"*501},
                                {"reason":"nul\x00"}, {"reason":"No\nhay"}, {"reason":False},
                                {"reason":123}, {"reason":[]}, {"reason":{}},
                                {"status":"Aceptado"},{"actor_account_id":1}])
def test_invalid_reason_or_administrative_fields_do_not_change_the_order(backend, body):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b,"staff"))
    assert decide(b,receipt,headers,"reject",body).status_code == 422
    with b.sessions() as db:
        assert db.get(Order,UUID(receipt["order_id"])).status == "Solicitud recibida"


def test_required_rejection_reason_and_query_validation(backend):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b,"admin"))
    assert decide(b,receipt,headers,"reject",{}).status_code == 422
    assert decide(b,receipt,headers,"reject",{"reason":None}).status_code == 422
    rejected = decide(b,receipt,headers,"reject",{"reason":"  Sin capacidad  "})
    assert rejected.status_code == 200 and rejected.json()["history"][-1]["reason"] == "Sin capacidad"
    assert b.client.get(f"{PATH}/{uuid4()}").status_code == 404
    assert decide(b,{"order_id":str(uuid4())},headers).status_code == 404
    for query in ({"limit":0},{"limit":101},{"offset":-1},{"status":""}):
        assert b.client.get(PATH,params=query).status_code == 422
    assert b.client.get(PATH,params={"status":"Sin coincidencias"}).json()["items"] == []


def test_order_operations_do_not_require_catalog_administration_to_be_enabled(backend):
    b = backend
    guest_order(b)
    config = b.config.model_copy(update={"auth_catalog_enabled":False,"auth_images_enabled":False})
    app = create_app(config)
    app.state.auth_service.clock = lambda:b.now.value
    with TestClient(app,base_url="http://localhost:8000",client=("127.0.0.1",54321)) as client:
        assert login(b,"staff",client=client).status_code == 200
        assert client.get(PATH).status_code == 200
        assert client.get("/api/admin/tags").status_code == 404
        preflight = client.options(f"{PATH}/{uuid4()}/accept",headers={"Origin":ORIGIN,
            "Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"Content-Type,X-CSRF-Token"})
        assert preflight.status_code == 200 and preflight.headers["access-control-allow-credentials"] == "true"


def test_history_failure_rolls_back_status_and_does_not_leak_database_errors(backend, caplog):
    b = backend
    receipt = guest_order(b)
    headers = csrf_headers(login(b,"admin"))
    def fail(connection,cursor,statement,parameters,context,many):
        if statement.lstrip().startswith('INSERT INTO "OrderStatusHistories"'):
            raise OperationalError("private-history-error",{},Exception("private-history-error"))
    engine = b.service.sessions.kw["bind"]
    event.listen(engine,"before_cursor_execute",fail)
    try:
        response = decide(b,receipt,headers)
    finally:
        event.remove(engine,"before_cursor_execute",fail)
    assert response.status_code == 503 and "private-history-error" not in response.text+caplog.text
    with b.sessions() as db:
        assert db.get(Order,UUID(receipt["order_id"])).status == "Solicitud recibida"
        assert len(db.scalars(select(OrderStatusHistory)).all()) == 1
    assert decide(b,receipt,headers).status_code == 200


def test_registered_order_keeps_coupon_history_and_idempotent_receipt_after_acceptance(backend):
    b = backend
    customer = csrf_headers(login(b,"customer"))
    key = str(uuid4())
    body = {"cart":b.selection["cart"],"data":{"modality":"dine_in","name":"Cliente"},
            "customer_coupon_id":b.coupon.customer_coupon_id}
    created = b.client.post("/api/orders",headers=customer | {"Idempotency-Key":key},json=body)
    assert created.status_code == 201 and created.json()["total"] == "145.00", created.text
    receipt = created.json()
    response = decide(b,receipt,csrf_headers(login(b,"staff")))
    assert response.status_code == 200 and response.json()["status"] == "Confirmado", response.text
    assert response.json()["coupon"] == receipt["coupon"] and response.json()["total"] == "145.00"
    assert [row["actor_account_id"] for row in response.json()["history"]] == [b.ids["customer"][0],b.ids["staff"][0]]
    customer = csrf_headers(login(b,"customer"))
    repeated = b.client.post("/api/orders",headers=customer | {"Idempotency-Key":key},json=body)
    assert repeated.status_code == 201 and repeated.json()["status"] == "Confirmado"
    assert repeated.json()["coupon"] == receipt["coupon"]
    with b.sessions() as db:
        assert len(db.scalars(select(Order)).all()) == 1 and len(db.scalars(select(OrderStatusHistory)).all()) == 2
        assert db.get(CustomerCoupon,b.coupon.customer_coupon_id).status == "available"


@pytest.mark.parametrize("actions", [(True,True),(True,False),(False,False),(True,None),(False,None)])
def test_concurrent_staff_decisions_and_customer_cancellation_have_one_winner(backend, actions):
    b = backend
    receipt = guest_order(b)
    identifier = UUID(receipt["order_id"])
    barrier = Barrier(2)
    def send(index, action):
        try:
            with b.sessions() as db,db.begin():
                barrier.wait(timeout=5)
                if action is None:
                    order_cancellation_service.cancel_order(db,identifier,guest_token=receipt["tracking"]["token"])
                    return "Cancelado"
                staff_order_service.decide_order(db,identifier,accepted=action,
                    actor_account_id=b.ids["staff" if index else "admin"][0],
                    reason="Motivo sintético de rechazo" if action is False else None)
                return "Confirmado" if action else "Rechazado"
        except (AuthError,order_cancellation_service.OrderNotPendingError) as error:
            assert not isinstance(error,AuthError) or error.status_code == 409
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send,index,action) for index,action in enumerate(actions)]
        results = [future.result(timeout=15) for future in futures]
    assert results.count("conflict") == 1
    with b.sessions() as db:
        row = db.get(Order,identifier)
        assert row.status in results
        history = db.scalars(select(OrderStatusHistory).order_by(OrderStatusHistory.created_at)).all()
        assert [entry.status for entry in history] == ["Solicitud recibida",row.status]

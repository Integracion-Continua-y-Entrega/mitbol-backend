"""HU-018: contratos públicos sin base de datos y compatibilidad con sesiones."""

from fastapi.testclient import TestClient
from pydantic import TypeAdapter
import pytest
from sqlalchemy.engine import Engine

from app.db.database import get_db
from app.main import create_app
from app.schemas.order_modality import OrderModalityInput, PickupData
from test_auth_api import backend as registered_backend, login, ORIGIN, password_hash
from test_auth_persistence import auth_database
from test_cloud_configuration import cloud_settings


PATH = "/api/order-modality/validate"


@pytest.fixture
def modality_client(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La validación de modalidad no debe consultar PostgreSQL")
    monkeypatch.setattr(Engine, "connect", forbidden)
    app = create_app(cloud_settings())
    app.dependency_overrides[get_db] = forbidden
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("payload,normalized", [
    ({"modality": "dine_in", "name": "  Ana  "}, {"modality": "dine_in", "name": "Ana"}),
    ({"modality": "dine_in", "name": "Cliente 7"}, {"modality": "dine_in", "name": "Cliente 7"}),
    ({"modality": "dine_in", "name": "José 🥣"}, {"modality": "dine_in", "name": "José 🥣"}),
    ({"modality": "dine_in", "name": "A" * 100}, {"modality": "dine_in", "name": "A" * 100}),
    ({"modality": "pickup", "name": "  María López ", "phone": " +52 (228) 123-4567 "},
     {"modality": "pickup", "name": "María López", "phone": "+522281234567"}),
    ({"modality": "pickup", "name": "Ana", "phone": "2281234567"},
     {"modality": "pickup", "name": "Ana", "phone": "2281234567"}),
    ({"modality": "pickup", "name": "Ana", "phone": "5551234"},
     {"modality": "pickup", "name": "Ana", "phone": "5551234"}),
    ({"modality": "pickup", "name": "Ana", "phone": "+123456789012345"},
     {"modality": "pickup", "name": "Ana", "phone": "+123456789012345"}),
])
def test_guests_can_validate_modality_without_database(modality_client, payload, normalized):
    response = modality_client.post(PATH, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == {"valid": True, "data": normalized}
    assert "authorization" not in response.request.headers and "cookie" not in response.request.headers
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("modality", ["delivery", "local", "DINE_IN", "pickup ", "", None, True, 1])
def test_unsupported_modalities_are_rejected(modality_client, modality):
    assert modality_client.post(PATH, json={"modality": modality, "name": "Ana"}).status_code == 422


@pytest.mark.parametrize("modality", ["dine_in", "pickup"])
@pytest.mark.parametrize("name", ["", "   ", None, True, 123, "A" * 101, "Ana\nLópez", "Ana\x00López", "\u200b"])
def test_invalid_names_are_rejected_for_both_modalities(modality_client, modality, name):
    payload = {"modality": modality, "name": name}
    if modality == "pickup":
        payload["phone"] = "2281234567"
    assert modality_client.post(PATH, json=payload).status_code == 422


@pytest.mark.parametrize("phone", ["", "   ", None, True, 2281234567, "abcdefg", "555abc1234",
                                    "123456", "1234567890123456", "123+4567", "+52\n2281234567"])
def test_invalid_pickup_phones_are_rejected(modality_client, phone):
    assert modality_client.post(PATH, json={"modality": "pickup", "name": "Ana", "phone": phone}).status_code == 422


@pytest.mark.parametrize("payload", [
    {}, {"name": "Ana"}, {"modality": "dine_in"},
    {"modality": "pickup", "phone": "2281234567"},
    {"modality": "pickup", "name": "Ana"},
])
def test_required_fields_are_not_inferred(modality_client, payload):
    assert modality_client.post(PATH, json=payload).status_code == 422


@pytest.mark.parametrize("modality", ["dine_in", "pickup"])
@pytest.mark.parametrize("field,value", [("address", "Calle 1"), ("table_number", 3),
                                        ("delivery_instructions", "Entregar en casa"), ("account_id", 1)])
def test_unneeded_or_client_supplied_identity_fields_are_rejected(modality_client, modality, field, value):
    payload = {"modality": modality, "name": "Ana", field: value}
    if modality == "pickup":
        payload["phone"] = "2281234567"
    assert modality_client.post(PATH, json=payload).status_code == 422


def test_dine_in_contract_contains_only_modality_and_name(modality_client):
    response = modality_client.post(PATH, json={"modality": "dine_in", "name": "Alias"})
    assert response.status_code == 200
    assert set(response.json()["data"]) == {"modality", "name"}
    assert modality_client.post(PATH, json={"modality": "dine_in", "name": "Alias", "phone": "2281234567"}).status_code == 422


def test_openapi_documents_public_discriminated_contract(modality_client):
    operation = modality_client.get("/openapi.json").json()["paths"][PATH]["post"]
    assert "security" not in operation
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["discriminator"]["propertyName"] == "modality"
    assert set(schema["discriminator"]["mapping"]) == {"dine_in", "pickup"}


def test_contract_can_be_reused_without_http():
    data = TypeAdapter(OrderModalityInput).validate_python(
        {"modality": "pickup", "name": " Ana ", "phone": "+52 (228) 123-4567"})
    assert isinstance(data, PickupData)
    assert data.model_dump() == {"modality": "pickup", "name": "Ana", "phone": "+522281234567"}


@pytest.mark.parametrize("role", ["admin", "staff", "customer"])
def test_registered_users_can_validate_without_accessing_or_changing_their_account(registered_backend, monkeypatch, role):
    b = registered_backend
    signed_in = login(b, role)
    assert signed_in.status_code == 200
    def forbidden(*args, **kwargs):
        pytest.fail("La validación de modalidad no debe consultar ni modificar cuentas")
    b.app.dependency_overrides[get_db] = forbidden
    with monkeypatch.context() as patch:
        patch.setattr(b.service, "sessions", forbidden)
        response = b.client.post(PATH, headers={"Origin": ORIGIN},
                                 json={"modality": "pickup", "name": "Otro nombre para este pedido", "phone": "2281234567"})
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    me = b.client.get("/api/auth/me", headers={"Origin": ORIGIN})
    assert me.status_code == 200 and me.json()["user"] == signed_in.json()["user"]

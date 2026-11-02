"""Diagnóstico completo con datos simulados; sólo se permiten tres GET."""

from copy import deepcopy
import json

import httpx
import pytest
from sqlalchemy.engine import Engine

from app.services.catalog_probe import inspect_catalog, select_store
from app.services.loyverse_client import (
    LoyverseAPIClient, LoyverseConfigurationError, LoyverseResponseError,
)


@pytest.fixture
def payloads():
    return {
        "/stores": {"stores": [{"id": "store-1", "name": "Tienda de prueba", "phone_number": "PRIVATE"}]},
        "/items": {"items": [{
            "id": "item-1", "item_name": "Producto", "modifiers_ids": ["group-1"],
            "variants": [{
                "variant_id": "variant-1", "item_id": "item-1", "option1_value": "Grande",
                "default_pricing_type": "FIXED", "default_price": 9.5,
                "stores": [{"store_id": "store-1", "pricing_type": "FIXED", "price": 10.5, "available_for_sale": True}],
                "cost": "PRIVATE",
            }],
        }]},
        "/modifiers": {"modifiers": [{
            "id": "group-1", "name": "Extras", "position": 1, "stores": ["store-1"],
            "modifier_options": [{"id": "option-1", "name": "Sin cebolla", "position": 1, "price": 0}],
        }]},
    }


async def probe(payloads, store_id="store-1"):
    def respond(request):
        assert request.method == "GET"
        endpoint = request.url.path.removeprefix("/v1.0")
        assert endpoint in {"/stores", "/items", "/modifiers"}
        return httpx.Response(200, json=deepcopy(payloads[endpoint]))

    async with LoyverseAPIClient("test-token", transport=httpx.MockTransport(respond)) as client:
        return await inspect_catalog(client, store_id)


@pytest.mark.asyncio
async def test_probe_observes_store_prices_and_relationships_without_database(payloads, monkeypatch):
    def forbidden_connection(*args, **kwargs):
        pytest.fail("El diagnóstico intentó acceder a una base de datos")

    monkeypatch.setattr(Engine, "connect", forbidden_connection)
    report = await probe(payloads)
    assert report["store"]["id"] == "store-1"
    assert report["counts"] == {
        "items": 1, "variants": 1, "modifier_groups": 1,
        "modifier_options": 1, "article_modifier_links": 1,
    }
    variant = report["items"][0]["variants"][0]
    assert variant["store_price"] == "10.5"
    assert variant["default_price"] == "9.5"
    assert variant["store_price_differs_from_default"] is True
    assert variant["price_source"] == "variants[].stores[].price"
    assert report["modifiers"][0]["options"][0]["price"] == "0"
    assert report["issues"] == []
    assert report["http_requests"] == 3
    assert "PRIVATE" not in json.dumps(report)
    assert "test-token" not in json.dumps(report)


@pytest.mark.asyncio
async def test_null_modifier_ids_do_not_create_assignments(payloads):
    payloads["/items"]["items"][0]["modifiers_ids"] = None
    report = await probe(payloads)
    assert report["items"][0]["modifier_ids"] == []
    assert report["unused_modifier_ids"] == ["group-1"]
    assert report["counts"]["article_modifier_links"] == 0


@pytest.mark.asyncio
async def test_actual_modifier_ids_field_is_supported_and_traced(payloads):
    item = payloads["/items"]["items"][0]
    item["modifier_ids"] = item.pop("modifiers_ids")
    report = await probe(payloads)
    assert report["items"][0]["modifier_ids_field"] == "modifier_ids"
    assert report["items"][0]["modifier_ids"] == ["group-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "contradictory"])
async def test_missing_or_contradictory_modifier_relationship_is_rejected(payloads, mode):
    item = payloads["/items"]["items"][0]
    if mode == "missing":
        del item["modifiers_ids"]
    else:
        item["modifier_ids"] = []
    with pytest.raises(LoyverseResponseError):
        await probe(payloads)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [
    ("null", "unknown_store_price"),
    ("variable", "variable_price"),
    ("other-store", "missing_store_configuration"),
])
async def test_incompatible_prices_are_reported_without_using_default_or_zero(payloads, mode, code):
    store = payloads["/items"]["items"][0]["variants"][0]["stores"][0]
    store["price"] = None
    if mode == "variable":
        store["pricing_type"] = "VARIABLE"
    if mode == "other-store":
        store["store_id"] = "store-2"
    report = await probe(payloads)
    variant = report["items"][0]["variants"][0]
    assert variant["store_price"] is None
    assert variant["price_source"] is None
    assert variant["default_price"] == "9.5"
    assert report["issues"][0]["code"] == code


@pytest.mark.asyncio
async def test_store_price_can_be_defined_when_default_is_null(payloads):
    payloads["/items"]["items"][0]["variants"][0]["default_price"] = None
    report = await probe(payloads)
    assert report["items"][0]["variants"][0]["store_price"] == "10.5"
    assert report["issues"] == []


@pytest.mark.asyncio
async def test_missing_required_price_is_an_incomplete_response(payloads):
    del payloads["/items"]["items"][0]["variants"][0]["stores"][0]["price"]
    with pytest.raises(LoyverseResponseError, match="Falta el importe"):
        await probe(payloads)


@pytest.mark.asyncio
async def test_variant_cannot_claim_another_parent_item(payloads):
    payloads["/items"]["items"][0]["variants"][0]["item_id"] = "another-item"
    with pytest.raises(LoyverseResponseError, match="artículo diferente"):
        await probe(payloads)


@pytest.mark.asyncio
async def test_duplicate_variants_are_rejected(payloads):
    item = payloads["/items"]["items"][0]
    item["variants"].append(deepcopy(item["variants"][0]))
    with pytest.raises(LoyverseResponseError, match="duplicado"):
        await probe(payloads)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [
    ("absent", "missing_modifier"), ("other-store", "modifier_not_in_store"),
])
async def test_modifier_relationships_are_not_invented(payloads, mode, code):
    if mode == "absent":
        payloads["/modifiers"]["modifiers"] = []
    else:
        payloads["/modifiers"]["modifiers"][0]["stores"] = ["store-2"]
    report = await probe(payloads)
    assert report["issues"][0]["code"] == code
    assert report["items"][0]["modifier_ids"] == ["group-1"]


@pytest.mark.asyncio
async def test_unknown_option_price_is_reported_as_null(payloads):
    payloads["/modifiers"]["modifiers"][0]["modifier_options"][0]["price"] = None
    report = await probe(payloads)
    assert report["modifiers"][0]["options"][0]["price"] is None
    assert report["issues"][0]["code"] == "unknown_modifier_option_price"


@pytest.mark.asyncio
async def test_empty_catalog_is_a_valid_observation(payloads):
    payloads["/items"]["items"] = []
    payloads["/modifiers"]["modifiers"] = []
    report = await probe(payloads)
    assert report["counts"]["items"] == 0
    assert report["issues"] == []


def test_store_selection_requires_unambiguous_configuration():
    stores = [{"id": "one", "name": "Uno"}, {"id": "two", "name": "Dos"}]
    with pytest.raises(LoyverseConfigurationError):
        select_store(stores, None)
    assert select_store(stores, "two")["name"] == "Dos"
    with pytest.raises(LoyverseConfigurationError):
        select_store(stores, "missing")
    stores[1]["deleted_at"] = "2026-01-01T00:00:00Z"
    with pytest.raises(LoyverseConfigurationError):
        select_store(stores, "two")
    assert select_store(stores, None)["id"] == "one"

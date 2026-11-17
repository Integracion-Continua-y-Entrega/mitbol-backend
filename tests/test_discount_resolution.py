"""HTTP sintético: reconciliación por identidad, sin red ni bases reales."""

from copy import deepcopy
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.discount_probe import fetch_discount_snapshot
from app.services.discount_transform import DiscountValidationError
from app.services.loyverse_client import LoyverseAPIClient, LoyverseAPIError, LoyverseResponseError
from test_discount_transform import DISCOUNT_ID, STORE_ID, source_discount


OTHER_ID = "b3a56a06-d40c-4c07-931c-41abcbaaba12"


class DiscountAPI:
    def __init__(self, rows, detail=None, *, status=200, stores=None, cursor=None):
        self.rows, self.detail, self.status = rows, detail, status
        self.stores = stores if stores is not None else [dict(id=STORE_ID, name="Tienda sintética")]
        self.cursor, self.requests = cursor, []

    def handle(self, request):
        assert request.method == "GET" and request.url.host == "api.loyverse.com"
        self.requests.append(request)
        if request.url.path == "/v1.0/stores":
            return httpx.Response(200, json={"stores": self.stores})
        if request.url.path == "/v1.0/discounts":
            return httpx.Response(200, json={"discounts": self.rows, "cursor": self.cursor})
        assert request.url.path == "/v1.0/discounts/" + DISCOUNT_ID
        return httpx.Response(self.status, json=self.detail)

    def client(self):
        return LoyverseAPIClient("synthetic-private-token", max_attempts=1,
                                 transport=httpx.MockTransport(self.handle))


def conflicts():
    current = source_discount(name="Descuento de prueba 10%", discount_percent=10,
                              updated_at="2026-11-15T19:12:19Z")
    previous = source_discount(name="Anterior", stores=[], discount_percent=20,
        updated_at="2026-10-22T17:13:30Z", deleted_at="2026-11-15T19:12:19Z")
    return current, previous


@pytest.mark.asyncio
async def test_identical_duplicates_normalize_uuid_without_individual_http_or_mutation():
    first = source_discount(discount_percent=10)
    second = {**first, "id": first["id"].upper()}
    api = DiscountAPI([first, second]); before = deepcopy(api.rows)
    async with api.client() as client:
        snapshot = await fetch_discount_snapshot(client)
    assert len(snapshot.records) == 1 and snapshot.records[0].loyverse_id == DISCOUNT_ID
    assert snapshot.resolutions[0]["reason"] == "DISCOUNT_IDENTICAL_COPIES"
    assert snapshot.source_count == 2 and not snapshot.quarantined
    assert len(api.requests) == 1 and api.rows == before


@pytest.mark.asyncio
@pytest.mark.parametrize("chosen_index", [0, 1])
async def test_conflict_accepts_valid_individual_not_automatically_active_or_newest(chosen_index):
    versions = conflicts()
    api = DiscountAPI(list(versions), versions[chosen_index])
    async with api.client() as client:
        snapshot = await fetch_discount_snapshot(client, store_id=STORE_ID)
    assert snapshot.records[0].value == Decimal(10 if chosen_index == 0 else 20)
    assert snapshot.records[0].is_deleted is (chosen_index == 1)
    assert snapshot.verified_store_id == STORE_ID
    assert snapshot.resolutions[0]["originals"] == list(versions)
    assert snapshot.resolutions[0]["selected"] == versions[chosen_index]
    assert len(api.requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("case,reason", [
    ("404", "DISCOUNT_DETAIL_NOT_FOUND"),
    ("different-id", "DISCOUNT_DETAIL_INVALID"),
    ("not-observed", "DISCOUNT_DETAIL_NOT_OBSERVED"),
    ("invalid-price", "DISCOUNT_DETAIL_INVALID"),
    ("bad-shape", "DISCOUNT_DETAIL_INVALID"),
])
async def test_unresolved_conflict_isolates_entire_id_without_inventing_deletion(case, reason):
    versions = conflicts(); detail = deepcopy(versions[0]); status = 200
    if case == "404": status = 404
    elif case == "different-id": detail["id"] = OTHER_ID
    elif case == "not-observed": detail["name"] = "Cambio concurrente"
    elif case == "invalid-price": detail["discount_percent"] = -1
    else: detail = []
    api = DiscountAPI(list(versions), detail, status=status)
    async with api.client() as client:
        snapshot = await fetch_discount_snapshot(client, collect_issues=True)
    assert snapshot.records == () and len(snapshot.quarantined) == 1
    assert snapshot.quarantined[0]["loyverse_id"] == DISCOUNT_ID
    assert snapshot.quarantined[0]["reason"] == reason
    assert snapshot.quarantined[0]["originals"] == list(versions)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429, 503])
async def test_individual_provider_failure_cannot_be_hidden_as_partial_success(status):
    api = DiscountAPI(list(conflicts()), {}, status=status)
    async with api.client() as client:
        with pytest.raises(LoyverseAPIError) as caught:
            await fetch_discount_snapshot(client, collect_issues=True)
    assert caught.value.status_code == status
    assert DISCOUNT_ID not in str(caught.value) and "synthetic-private-token" not in str(caught.value)


@pytest.mark.asyncio
async def test_unknown_type_is_preserved_without_extending_enum_or_guessing_value():
    unknown = source_discount(type="TEYA_DISCOUNT", discount_percent=None, id=OTHER_ID)
    api = DiscountAPI([source_discount(discount_percent=10), unknown])
    async with api.client() as client:
        snapshot = await fetch_discount_snapshot(client, collect_issues=True)
    assert len(snapshot.records) == 1 and snapshot.records[0].value == Decimal(10)
    assert snapshot.quarantined[0]["originals"] == [unknown]
    assert snapshot.quarantined[0]["reason"] == "DISCOUNT_UNSUPPORTED_TYPE"
    assert len(api.requests) == 1
    async with api.client() as client:
        with pytest.raises(DiscountValidationError, match="estricto"):
            await fetch_discount_snapshot(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"id": "invalid"}, {"id": None}, None])
async def test_malformed_bulk_identity_or_structure_aborts_all_before_individual_get(bad):
    api = DiscountAPI([*conflicts(), bad])
    async with api.client() as client:
        with pytest.raises((DiscountValidationError, LoyverseResponseError)):
            await fetch_discount_snapshot(client, collect_issues=True)
    assert len(api.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor", ["next", {}, True])
async def test_partial_policy_does_not_relax_incomplete_pagination(cursor):
    api = DiscountAPI([source_discount(discount_percent=10)], cursor=cursor)
    async with api.client() as client:
        with pytest.raises(LoyverseResponseError):
            await fetch_discount_snapshot(client, collect_issues=True)


@pytest.mark.asyncio
async def test_store_must_be_accessible_before_download():
    api = DiscountAPI([], stores=[])
    async with api.client() as client:
        with pytest.raises(LoyverseAPIError):
            await fetch_discount_snapshot(client, store_id=STORE_ID, collect_issues=True)
    assert len(api.requests) == 1 and api.requests[0].url.path.endswith("/stores")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["https://external.invalid/", "../../stores", "", None])
async def test_individual_method_never_opens_arbitrary_routes(invalid):
    api = DiscountAPI([])
    async with api.client() as client:
        with pytest.raises((ValueError, LoyverseAPIError)):
            await client.get_discount(invalid)
        with pytest.raises(ValueError):
            await client.get("/discounts/" + DISCOUNT_ID)
        assert client.request_count == 0


@pytest.mark.asyncio
async def test_individual_transport_uses_existing_bounded_retry_policy():
    requests = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=source_discount(discount_percent=10))
    sleep = AsyncMock()
    async with LoyverseAPIClient("synthetic", sleep=sleep, transport=httpx.MockTransport(handle)) as client:
        result = await client.get_discount(DISCOUNT_ID.upper())
    assert result["id"] == DISCOUNT_ID and len(requests) == 2
    sleep.assert_awaited_once_with(1)

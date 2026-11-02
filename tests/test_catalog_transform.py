"""Transformación comercial pura: no necesita una base ni credenciales de API."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.catalog_errors import CatalogValidationError
from app.services.catalog_transform import transform_catalog


def test_plan_contains_decimal_prices_and_real_relationships(commercial_snapshot):
    plan = transform_catalog(commercial_snapshot)
    assert (len(plan.products), len(plan.variants), len(plan.modifiers), len(plan.complements)) == (3, 5, 2, 5)
    assert all(isinstance(value.price, Decimal) for value in (*plan.variants, *plan.complements))
    assert {(row.loyverse_id, row.product_loyverse_id) for row in plan.variants} == {
        ("single", "burger"), ("double", "burger"), ("small", "drink"),
        ("large", "drink"), ("regular", "fries"),
    }
    assert set(plan.assignments) == {
        ("single", "extras"), ("double", "extras"), ("small", "herbs"), ("large", "herbs"),
    }
    assert next(row for row in plan.variants if row.loyverse_id == "regular").name == "Papas"
    assert next(row for row in plan.complements if row.loyverse_id == "no-onion").price == Decimal("0")


@pytest.mark.parametrize("value", [None, -1, Decimal("NaN"), Decimal("Infinity"), 1.1, "12.00", True])
def test_invalid_price_never_becomes_zero(commercial_snapshot, value):
    snapshot = deepcopy(commercial_snapshot)
    snapshot.items[0]["variants"][0]["stores"][0]["price"] = value
    with pytest.raises(CatalogValidationError):
        transform_catalog(snapshot)


def test_store_override_and_precision_are_preserved(commercial_snapshot):
    snapshot = deepcopy(commercial_snapshot)
    snapshot.items[0]["variants"][0]["stores"][0]["price"] = Decimal("90.1234567890123456789")
    plan = transform_catalog(snapshot)
    assert plan.variants[0].price == Decimal("90.1234567890123456789")
    assert snapshot.items[0]["variants"][0]["default_price"] == 89


@pytest.mark.parametrize("mode", ["missing", "variable", "foreign-store", "missing-modifier", "duplicate-variant"])
def test_incompatible_data_is_rejected(commercial_snapshot, mode):
    snapshot = deepcopy(commercial_snapshot)
    variant = snapshot.items[0]["variants"][0]
    if mode == "missing":
        del variant["stores"][0]["price"]
    elif mode == "variable":
        variant["stores"][0]["pricing_type"] = "VARIABLE"
    elif mode == "foreign-store":
        variant["stores"][0]["store_id"] = "another-store"
    elif mode == "missing-modifier":
        snapshot.items[0]["modifier_ids"] = ["not-returned"]
    else:
        snapshot.items[0]["variants"].append(deepcopy(variant))
    with pytest.raises(CatalogValidationError):
        transform_catalog(snapshot)


def test_persistence_requires_deleted_records_and_aware_timestamp(commercial_snapshot):
    with pytest.raises(CatalogValidationError, match="show_deleted"):
        transform_catalog(replace(commercial_snapshot, includes_deleted=False))
    with pytest.raises(CatalogValidationError, match="zona horaria"):
        transform_catalog(replace(commercial_snapshot, fetched_at=commercial_snapshot.fetched_at.replace(tzinfo=None)))


def test_sparse_tombstones_need_no_invented_fields(commercial_snapshot):
    deleted = {"id": "never-imported", "deleted_at": "2026-11-01T00:00:00Z"}
    snapshot = replace(commercial_snapshot, items=(deleted,), modifiers=(deleted,))
    plan = transform_catalog(snapshot)
    assert not plan.products and not plan.variants and not plan.modifiers
    assert plan.deleted_products == ("never-imported",)
    assert plan.deleted_modifiers == ("never-imported",)


def test_transform_does_not_mutate_the_download(commercial_snapshot):
    original = deepcopy(commercial_snapshot)
    transform_catalog(commercial_snapshot)
    assert commercial_snapshot == original


def test_names_are_not_silently_truncated(commercial_snapshot):
    snapshot = deepcopy(commercial_snapshot)
    snapshot.items[0]["item_name"] = "a" * 151
    with pytest.raises(CatalogValidationError):
        transform_catalog(snapshot)

"""Preparación completa por tienda sin escribir ni modificar el snapshot."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.discount_batch import prepare_discount_batch
from app.services.discount_probe import DiscountSnapshot
from app.services.discount_rules import DiscountType
from app.services.discount_transform import DiscountRecord, DiscountValidationError


STORE = "a3a56a06-d40c-4c07-931c-41abcbaaba12"
OTHER = "497f6eca-6276-4993-bfeb-53cbbbba6f08"
STAMP = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def record(**changes):
    base = DiscountRecord(
        loyverse_id=OTHER, name="Prueba", discount_type=DiscountType.FIXED_AMOUNT,
        value=Decimal("35.25"), stores=(STORE,), restricted_access=False,
        created_at=STAMP, updated_at=STAMP, deleted_at=None,
    )
    return replace(base, **changes)


def test_batch_preserves_rows_and_uses_snapshot_time_without_mutation():
    records = (
        record(), record(stores=(OTHER,)), record(stores=()),
        record(stores=(), deleted_at=STAMP),
        record(discount_type=DiscountType.VARIABLE_AMOUNT, value=None),
    )
    snapshot = DiscountSnapshot(records, True, STAMP)
    rows = prepare_discount_batch(snapshot, STORE)
    assert isinstance(rows, tuple)
    assert len(rows) == len(records)
    assert [row["is_active"] for row in rows] == [True, False, True, False, True]
    assert all(row["synced_at"] == STAMP for row in rows)
    assert all(row["scope"] == "unknown" for row in rows)
    assert rows[0]["value"] == Decimal("35.25")
    assert rows[-1]["value"] is None
    assert all("created_at" not in row and "discount_id" not in row for row in rows)
    assert snapshot.records == records


def test_empty_snapshot_returns_empty_tuple():
    assert prepare_discount_batch(DiscountSnapshot((), True, STAMP), STORE) == ()


def test_invalid_store_rejects_batch():
    snapshot = DiscountSnapshot((record(), record(stores=())), True, STAMP)
    with pytest.raises(DiscountValidationError):
        prepare_discount_batch(snapshot, "invalid-store")
    assert snapshot.records[0].stores == (STORE,)


def test_naive_snapshot_time_is_rejected():
    snapshot = DiscountSnapshot((record(),), True, STAMP.replace(tzinfo=None))
    with pytest.raises(DiscountValidationError):
        prepare_discount_batch(snapshot, STORE)

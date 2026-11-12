"""Contrato administrativo aislado, sin registro del modelo ni base de datos."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.discounts import DiscountRead


def payload(**changes):
    data = dict(
        discount_id=1, loyverse_id="497f6eca-6276-4993-bfeb-53cbbbba6f08",
        name="Prueba", discount_type="FIXED_AMOUNT", value=Decimal("35.1234567890123456789"),
        scope="unknown", is_active=False, synced_at=None,
        created_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
    )
    data.update(changes)
    return data


def test_decimal_json_is_exact_and_attributes_are_supported():
    result = DiscountRead.model_validate(SimpleNamespace(**payload()))
    assert result.value == Decimal("35.1234567890123456789")
    serialized = result.model_dump(mode="json")
    assert serialized["value"] == "35.1234567890123456789"
    assert serialized["discount_type"] == "FIXED_AMOUNT"
    assert serialized["synced_at"] is None
    assert serialized["is_active"] is False


def test_variable_value_can_be_null():
    result = DiscountRead.model_validate(payload(discount_type="VARIABLE_AMOUNT", value=None))
    assert result.model_dump(mode="json")["value"] is None


@pytest.mark.parametrize("field", ["value", "synced_at"])
def test_nullable_fields_are_still_required(field):
    data = payload()
    del data[field]
    with pytest.raises(ValidationError) as error:
        DiscountRead.model_validate(data)
    assert error.value.errors()[0]["loc"] == (field,)
    assert error.value.errors()[0]["type"] == "missing"


@pytest.mark.parametrize("value", ["UNKNOWN", "fixed_amount"])
def test_unknown_discount_type_is_rejected(value):
    with pytest.raises(ValidationError):
        DiscountRead.model_validate(payload(discount_type=value))

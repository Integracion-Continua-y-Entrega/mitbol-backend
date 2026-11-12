"""Clasificación exacta de tipos, sin red ni base de datos."""

import pytest

from app.services.discount_rules import DiscountType, is_variable_discount


@pytest.mark.parametrize("raw,expected", [
    ("FIXED_PERCENT", False), ("FIXED_AMOUNT", False),
    ("VARIABLE_PERCENT", True), ("VARIABLE_AMOUNT", True),
    ("DISCOUNT_BY_POINTS", False),
])
def test_discount_type_and_variable_flag(raw, expected):
    kind = DiscountType(raw)
    assert kind.value == raw
    assert is_variable_discount(raw) is expected
    assert is_variable_discount(kind) is expected


@pytest.mark.parametrize("raw", ["", "UNKNOWN", "variable_amount", "VARIABLE_AMOUNT_EXTRA", None, True, 1])
def test_unknown_discount_types_are_not_silently_fixed(raw):
    with pytest.raises((ValueError, TypeError)):
        is_variable_discount(raw)

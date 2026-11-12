"""Datos sintéticos del proveedor: tipos, precisión y rechazo íntegro del lote."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.discount_rules import DiscountType
from app.services.discount_transform import (
    DiscountValidationError, discount_applies_to_store, discount_persistence_values,
    parse_discount, parse_discount_records,
)


DISCOUNT_ID = "497f6eca-6276-4993-bfeb-53cbbbba6f08"
STORE_ID = "a3a56a06-d40c-4c07-931c-41abcbaaba12"


def source_discount(**changes):
    record = dict(
        id=DISCOUNT_ID, name="Descuento sintético", type="FIXED_PERCENT",
        discount_percent=Decimal("10.5"), discount_amount=None,
        stores=[STORE_ID], restricted_access=False,
        created_at="2026-11-01T12:00:00Z", updated_at="2026-11-02T13:00:00Z",
        deleted_at=None,
    )
    record.update(changes)
    return record


@pytest.mark.parametrize("kind,amount,percent,expected,variable", [
    ("FIXED_PERCENT", None, Decimal("10.1234567890123456789"), Decimal("10.1234567890123456789"), False),
    ("FIXED_AMOUNT", Decimal("35.25"), None, Decimal("35.25"), False),
    ("VARIABLE_PERCENT", None, None, None, True),
    ("VARIABLE_AMOUNT", None, None, None, True),
    ("DISCOUNT_BY_POINTS", None, None, None, False),
])
def test_source_types_preserve_value_meaning(kind, amount, percent, expected, variable):
    source = source_discount(type=kind, discount_amount=amount, discount_percent=percent)
    before = deepcopy(source)
    result = parse_discount(source)
    assert result.discount_type is DiscountType(kind)
    assert result.value == expected
    if expected is not None:
        assert isinstance(result.value, Decimal)
    assert result.is_variable is variable
    assert result.is_deleted is False
    assert result.stores == (STORE_ID,)
    assert result.created_at.tzinfo is timezone.utc
    assert source == before


@pytest.mark.parametrize("kind,field,upper", [
    ("FIXED_PERCENT", "discount_percent", "100"),
    ("FIXED_AMOUNT", "discount_amount", "999999.99"),
])
@pytest.mark.parametrize("value", ["0.01", "upper"])
def test_fixed_value_provider_boundaries(kind, field, upper, value):
    amount = Decimal(upper if value == "upper" else value)
    source = source_discount(type=kind, discount_percent=None, discount_amount=None)
    source[field] = amount
    result = parse_discount(source)
    assert result.value == amount


@pytest.mark.parametrize("kind,field", [("FIXED_PERCENT", "discount_percent"), ("FIXED_AMOUNT", "discount_amount")])
@pytest.mark.parametrize("value", [None, True, False, "10.5", 10.5, 0, -1,
                                     Decimal("NaN"), Decimal("Infinity"), Decimal("sNaN"), Decimal("1000000")])
def test_invalid_fixed_values_are_not_coerced_to_zero(kind, field, value):
    record = source_discount(type=kind, discount_percent=None, discount_amount=None)
    record[field] = value
    with pytest.raises(DiscountValidationError):
        parse_discount(record)


def test_percentage_above_one_hundred_is_rejected():
    with pytest.raises(DiscountValidationError):
        parse_discount(source_discount(discount_percent=Decimal("100.000000000000000001")))


@pytest.mark.parametrize("kind", ["VARIABLE_PERCENT", "VARIABLE_AMOUNT", "DISCOUNT_BY_POINTS"])
@pytest.mark.parametrize("field", ["discount_percent", "discount_amount"])
def test_non_fixed_types_do_not_invent_catalog_values(kind, field):
    record = source_discount(type=kind, discount_percent=None, discount_amount=None)
    record[field] = Decimal("10")
    with pytest.raises(DiscountValidationError):
        parse_discount(record)


@pytest.mark.parametrize("field,value", [
    ("id", "not-a-uuid"), ("id", None), ("name", ""), ("name", "   "),
    ("name", "x" * 41), ("name", 9), ("type", "UNKNOWN"), ("type", None),
    ("stores", None), ("stores", "all"), ("stores", ["invalid"]),
    ("stores", [STORE_ID, STORE_ID.upper()]),
    ("restricted_access", "false"), ("restricted_access", None),
    ("created_at", "invalid"), ("created_at", "2026-11-01"),
    ("created_at", None), ("updated_at", "2026-11-02T12:00:00"),
    ("created_at", "0001-01-01T00:00:00+01:00"),
    ("deleted_at", "invalid"), ("deleted_at", "2026-11-02"),
])
def test_invalid_source_fields_reject_record(field, value):
    with pytest.raises(DiscountValidationError):
        parse_discount(source_discount(**{field: value}))


def test_deleted_and_restricted_are_independent():
    active = parse_discount(source_discount(restricted_access=True, stores=[]))
    assert active.restricted_access is True and active.is_deleted is False
    assert active.stores == ()  # Conserva información; no decide alcance ni tienda.
    deleted = parse_discount(source_discount(deleted_at="2026-11-03T09:00:00-06:00"))
    assert deleted.is_deleted is True
    assert deleted.deleted_at.isoformat() == "2026-11-03T15:00:00+00:00"


def test_batch_validates_completely_and_normalizes_ids_without_mutating_source():
    first = source_discount()
    second = source_discount(id=STORE_ID, type="VARIABLE_AMOUNT", discount_percent=None)
    records = [first, second]
    before = deepcopy(records)
    result = parse_discount_records(records)
    assert [row.loyverse_id for row in result] == [DISCOUNT_ID, STORE_ID]
    assert records == before
    with pytest.raises(FrozenInstanceError):
        result[0].name = "changed"


def test_duplicate_ids_are_rejected_even_with_different_case():
    with pytest.raises(DiscountValidationError, match="duplicado"):
        parse_discount_records([source_discount(), source_discount(id=DISCOUNT_ID.upper())])


def test_invalid_later_record_does_not_modify_earlier_record():
    records = [source_discount(), source_discount(id=STORE_ID, name="")]
    before = deepcopy(records)
    with pytest.raises(DiscountValidationError):
        parse_discount_records(records)
    assert records == before


@pytest.mark.parametrize("value", [None, {}, "discounts", [None], [1]])
def test_invalid_collections_are_rejected(value):
    with pytest.raises(DiscountValidationError):
        parse_discount_records(value)


def test_empty_collection_is_valid_without_implying_database_deletions():
    assert parse_discount_records([]) == ()


@pytest.mark.parametrize("value", [1, 35])
def test_json_integer_amounts_are_preserved_as_decimal(value):
    result = parse_discount(source_discount(type="FIXED_AMOUNT", discount_amount=value, discount_percent=None))
    assert result.value == Decimal(value)
    assert isinstance(result.value, Decimal)


def test_only_the_field_relevant_to_the_fixed_type_becomes_value():
    # La documentación muestra ambos campos; la semántica la determina type.
    result = parse_discount(source_discount(discount_percent=Decimal("5"), discount_amount=Decimal("99")))
    assert result.value == Decimal("5")


def test_rejection_does_not_echo_provider_payload():
    secret = "PRIVATE_SENSITIVE_PAYLOAD"
    with pytest.raises(DiscountValidationError) as error:
        parse_discount(source_discount(type=secret))
    assert secret not in str(error.value)


@pytest.mark.parametrize("applies,deleted,expected", [
    (True, None, True), (False, None, False),
    (True, "2026-11-03T12:00:00Z", False), (False, "2026-11-03T12:00:00Z", False),
])
def test_persistence_mapping_keeps_explicit_scope_values_and_store_state(applies, deleted, expected):
    record = parse_discount(source_discount(restricted_access=True, deleted_at=deleted))
    moment = datetime(2026, 11, 11, 9, tzinfo=timezone(timedelta(hours=-6)))
    values = discount_persistence_values(record, applies_to_store=applies, synced_at=moment)
    assert set(values) == {"loyverse_id", "name", "discount_type", "value", "scope", "is_active", "synced_at"}
    assert values["loyverse_id"] == DISCOUNT_ID
    assert values["name"] == record.name
    assert type(values["discount_type"]) is str
    assert values["discount_type"] == "FIXED_PERCENT"
    assert values["value"] is record.value
    assert values["scope"] == "unknown"
    assert values["is_active"] is expected
    assert values["synced_at"] == datetime(2026, 11, 11, 15, tzinfo=timezone.utc)
    assert values["synced_at"].tzinfo is timezone.utc


def test_variable_value_stays_none_in_database_values():
    record = parse_discount(source_discount(type="VARIABLE_AMOUNT", discount_percent=None))
    values = discount_persistence_values(record, applies_to_store=True, synced_at=datetime.now(timezone.utc))
    assert values["value"] is None and values["discount_type"] == "VARIABLE_AMOUNT"


@pytest.mark.parametrize("applies", [None, 1, "true"])
def test_persistence_mapping_requires_explicit_boolean(applies):
    with pytest.raises(DiscountValidationError):
        discount_persistence_values(parse_discount(source_discount()), applies_to_store=applies,
                                    synced_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("moment", [None, "2026-11-11T12:00:00Z", datetime(2026, 11, 11)])
def test_persistence_mapping_requires_aware_sync_time(moment):
    with pytest.raises(DiscountValidationError):
        discount_persistence_values(parse_discount(source_discount()), applies_to_store=True, synced_at=moment)


@pytest.mark.parametrize("stores,store,expected", [
    ([], STORE_ID, True), ([STORE_ID], STORE_ID, True),
    ([STORE_ID], STORE_ID.upper(), True), ([STORE_ID], DISCOUNT_ID, False),
])
def test_store_membership_is_explicit_and_empty_means_all(stores, store, expected):
    record = parse_discount(source_discount(stores=stores))
    assert discount_applies_to_store(record, store) is expected


def test_empty_store_list_does_not_authorize_invalid_store_identifier():
    with pytest.raises(DiscountValidationError):
        discount_applies_to_store(parse_discount(source_discount(stores=[])), "invalid")


def test_deleted_discount_in_all_stores_stays_inactive():
    record = parse_discount(source_discount(stores=[], deleted_at="2026-11-03T12:00:00Z"))
    values = discount_persistence_values(record, applies_to_store=discount_applies_to_store(record, STORE_ID),
                                         synced_at=datetime.now(timezone.utc))
    assert values["is_active"] is False

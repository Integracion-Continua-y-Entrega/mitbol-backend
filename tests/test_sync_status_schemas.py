"""Validación de observaciones sin atribuirlas a ejecuciones o resultados."""

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
import pytest

from app.schemas.sync_status import EntitySyncSummary, SyncEntity


def payload(**changes):
    values = dict(entity="Discounts", total_count=0, synced_count=0, last_synced_at=None)
    values.update(changes)
    return values


@pytest.mark.parametrize("total", [0, 3])
def test_no_known_dates_is_valid_for_empty_or_nonempty_entity(total):
    result = EntitySyncSummary.model_validate(payload(total_count=total))
    assert result.entity is SyncEntity.DISCOUNTS
    assert result.total_count == total
    assert result.model_dump(mode="json")["last_synced_at"] is None


def test_partial_observation_preserves_counts_and_normalizes_date():
    stamp = datetime(2026, 11, 12, 6, tzinfo=timezone(timedelta(hours=-6)))
    result = EntitySyncSummary.model_validate(payload(total_count=3, synced_count=2, last_synced_at=stamp))
    assert result.synced_count == 2
    assert result.last_synced_at == datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    assert result.model_dump(mode="json")["last_synced_at"] == "2026-11-12T12:00:00Z"


@pytest.mark.parametrize("field", ["total_count", "synced_count"])
@pytest.mark.parametrize("value", [-1, True, "3", 1.5])
def test_counts_require_nonnegative_integers(field, value):
    with pytest.raises(ValidationError):
        EntitySyncSummary.model_validate(payload(**{field: value}))


@pytest.mark.parametrize("changes", [
    dict(total_count=1, synced_count=2, last_synced_at="2026-11-12T12:00:00Z"),
    dict(total_count=0, synced_count=0, last_synced_at="2026-11-12T12:00:00Z"),
    dict(total_count=3, synced_count=1, last_synced_at=None),
])
def test_inconsistent_observation_is_rejected(changes):
    with pytest.raises(ValidationError):
        EntitySyncSummary.model_validate(payload(**changes))


@pytest.mark.parametrize("field", ["total_count", "synced_count", "last_synced_at"])
def test_all_observation_fields_are_required(field):
    values = payload()
    del values[field]
    with pytest.raises(ValidationError) as error:
        EntitySyncSummary.model_validate(values)
    assert error.value.errors()[0]["type"] == "missing"


def test_naive_date_is_rejected():
    with pytest.raises(ValidationError):
        EntitySyncSummary.model_validate(payload(total_count=1, synced_count=1, last_synced_at=datetime(2026, 11, 12)))


def test_unknown_entity_is_rejected():
    with pytest.raises(ValidationError):
        EntitySyncSummary.model_validate(payload(entity="Persons"))

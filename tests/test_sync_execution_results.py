"""Resultados verificables, sin anunciar una finalización por inferencia."""

from pydantic import ValidationError
import pytest

from app.schemas.sync_requests import ManualSyncResponse, completed_response


def test_discount_count_requires_exact_nonnegative_integer():
    assert completed_response("discounts", 0).result.discounts_processed == 0
    for value in [True, -1, "2", 2.5, {"token": "synthetic"}]:
        with pytest.raises(ValidationError):
            completed_response("discounts", value)


def test_worker_response_must_explicitly_confirm_completion():
    with pytest.raises(ValidationError):
        ManualSyncResponse.model_validate({"target": "discounts", "result": {"discounts_processed": 1}})


def test_wrong_result_for_target_is_rejected():
    with pytest.raises(ValidationError):
        ManualSyncResponse.model_validate({"target": "catalog", "status": "completed", "result": {"discounts_processed": 1}})

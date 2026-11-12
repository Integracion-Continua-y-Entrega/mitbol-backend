"""Contrato de solicitud: selección explícita, sin configuración del ejecutor."""

from pydantic import ValidationError
import pytest

from app.schemas.sync_requests import ManualSyncRequest


@pytest.mark.parametrize("target", ["catalog", "discounts"])
def test_known_target_is_accepted(target):
    assert ManualSyncRequest.model_validate({"target": target}).model_dump() == {"target": target}


def test_target_is_required():
    with pytest.raises(ValidationError) as error:
        ManualSyncRequest.model_validate({})
    assert error.value.errors(include_input=False)[0]["type"] == "missing"


@pytest.mark.parametrize("target", ["all", "", "CATALOG", "catalog ", None, 1, True, [], {}])
def test_unknown_or_invalid_target_is_rejected(target):
    with pytest.raises(ValidationError):
        ManualSyncRequest.model_validate({"target": target})


@pytest.mark.parametrize("field", ["database_url", "store_id", "token", "account_id"])
def test_request_cannot_override_server_configuration_or_actor(field):
    with pytest.raises(ValidationError) as error:
        ManualSyncRequest.model_validate({"target": "catalog", field: "synthetic-value"})
    assert error.value.errors(include_input=False)[0]["type"] == "extra_forbidden"

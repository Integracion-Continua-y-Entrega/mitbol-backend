"""Contrato QR sin datos personales; oculta el valor en repr y mensajes de validación."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.loyalty_identification import QR_VALUE_LENGTH, hash_identification


class LoyaltyIdentificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    qr_payload: Annotated[str, Field(min_length=QR_VALUE_LENGTH, max_length=QR_VALUE_LENGTH, repr=False)]

    @field_validator("qr_payload")
    @classmethod
    def canonical_qr(cls, value):
        hash_identification(value)
        return value


class LoyaltyIdentificationRead(LoyaltyIdentificationInput):
    identification_type: Literal["qr"] = "qr"
    purpose: Literal["visit_identification"] = "visit_identification"
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def explicit_validity(self):
        """El código debe incluir una vigencia inequívoca antes de mostrarse al cliente."""
        if (self.issued_at.utcoffset() is None or self.expires_at.utcoffset() is None
                or self.issued_at >= self.expires_at):
            raise ValueError("La identificación requiere fechas con zona horaria y vigencia positiva.")
        return self

"""Datos de atención reutilizables al registrar una solicitud en HU-021."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class OrderContactData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = Field(
        description="Nombre del solicitante; en consumo local también puede ser un alias."
    )

    @field_validator("name")
    @classmethod
    def printable_name(cls, value):
        if not value.isprintable():
            raise ValueError("El nombre no debe contener caracteres de control o invisibles.")
        return value


class DineInData(OrderContactData):
    modality: Literal["dine_in"]


class PickupData(OrderContactData):
    modality: Literal["pickup"]
    phone: str = Field(
        pattern=r"^\+?[0-9]{7,15}$",
        description="Teléfono con 7 a 15 dígitos y + inicial opcional; admite espacios, guiones y paréntesis al recibirlo."
    )

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value):
        if isinstance(value, str):
            return value.strip().translate(str.maketrans("", "", " ()-"))
        return value


OrderModalityInput = Annotated[DineInData | PickupData, Field(discriminator="modality")]


class OrderModalityValidationResponse(BaseModel):
    valid: Literal[True] = True
    data: OrderModalityInput

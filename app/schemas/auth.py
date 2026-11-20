"""Contratos de autenticación: entradas secretas y salidas explícitas."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from app.core.account_identity import normalize_email
from app.core.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class AuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class LoginInput(AuthInput):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value):
        return normalize_email(value)


class CustomerRegistrationInput(LoginInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    phone: str = Field(pattern=r"^\+?[0-9]{7,15}$", description="Teléfono de 7 a 15 dígitos; + inicial opcional.")
    password: SecretStr = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("name")
    @classmethod
    def printable_name(cls, value):
        if not value.isprintable():
            raise ValueError("El nombre no debe contener caracteres de control o invisibles.")
        return value

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value):
        if isinstance(value, str):
            return value.strip().translate(str.maketrans("", "", " ()-"))
        return value


class PasswordChangeInput(AuthInput):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=15, max_length=1024)


class UserResponse(BaseModel):
    account_id: int
    person_id: int
    email: str
    name: str
    roles: list[str]
    permissions: list[str]


class CustomerRegistrationResponse(BaseModel):
    user: UserResponse


class SessionResponse(BaseModel):
    user: UserResponse
    expires_at: datetime
    idle_expires_at: datetime


class LoginResponse(SessionResponse):
    csrf_token: str


class CsrfResponse(BaseModel):
    csrf_token: str

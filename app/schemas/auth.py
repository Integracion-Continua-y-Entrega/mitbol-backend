"""Contratos de autenticación: entradas secretas y salidas explícitas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.core.account_identity import normalize_email


class AuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class LoginInput(AuthInput):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value):
        return normalize_email(value)


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


class SessionResponse(BaseModel):
    user: UserResponse
    expires_at: datetime
    idle_expires_at: datetime


class LoginResponse(SessionResponse):
    csrf_token: str


class CsrfResponse(BaseModel):
    csrf_token: str

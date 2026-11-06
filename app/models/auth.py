"""Identidad compartida de MITBOL y persistencia de seguridad, sin acceso HTTP."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
    Integer, String, Text, Uuid, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.account_identity import normalize_email
from app.db.auth_base import AuthBase


class Person(AuthBase):
    __tablename__ = "Persons"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="ck_Persons_name_not_blank"),
        CheckConstraint("length(btrim(phone)) > 0", name="ck_Persons_phone_not_blank"),
    )

    person_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[PersonalAccount | None] = relationship(back_populates="person", passive_deletes="all")
    employee: Mapped[Employee | None] = relationship(back_populates="person", passive_deletes="all")


class PersonalAccount(AuthBase):
    __tablename__ = "PersonalAccounts"
    __table_args__ = (
        CheckConstraint("email = lower(btrim(email)) AND position('@' in email) > 1",
                        name="ck_PersonalAccounts_normalized_email"),
        CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_PersonalAccounts_argon2id"),
        CheckConstraint("auth_version >= 0", name="ck_PersonalAccounts_auth_version"),
    )

    account_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("Persons.person_id"), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    # Activación explícita: no inferir el DEFAULT recortado del diagrama.
    is_active: Mapped[bool] = mapped_column(Boolean)
    auth_version: Mapped[int] = mapped_column(Integer, server_default="0")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    person: Mapped[Person] = relationship(back_populates="account")
    sessions: Mapped[list[AccountSession]] = relationship(back_populates="account", passive_deletes="all")
    audit_events: Mapped[list[AuditEvent]] = relationship(back_populates="actor", passive_deletes="all")

    @validates("email")
    def canonical_email(self, key, value):
        return normalize_email(value)


class Employee(AuthBase):
    __tablename__ = "Employees"
    __table_args__ = (
        CheckConstraint("length(btrim(employee_code)) > 0", name="ck_Employees_code_not_blank"),
    )

    person_id: Mapped[int] = mapped_column(ForeignKey("Persons.person_id"), primary_key=True,
                                         autoincrement=False)
    employee_code: Mapped[str] = mapped_column(String(30), unique=True)
    hired_at: Mapped[date] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    person: Mapped[Person] = relationship(back_populates="employee")
    role_assignments: Mapped[list[EmployeeRole]] = relationship(back_populates="employee", passive_deletes="all")


class Role(AuthBase):
    __tablename__ = "Roles"
    __table_args__ = (
        CheckConstraint("code ~ '^[a-z][a-z0-9_]{0,29}$'", name="ck_Roles_code"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_Roles_name_not_blank"),
    )

    role_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)

    employee_assignments: Mapped[list[EmployeeRole]] = relationship(back_populates="role", passive_deletes="all")


class EmployeeRole(AuthBase):
    __tablename__ = "EmployeeRoles"

    person_id: Mapped[int] = mapped_column(ForeignKey("Employees.person_id"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("Roles.role_id"), primary_key=True)

    employee: Mapped[Employee] = relationship(back_populates="role_assignments")
    role: Mapped[Role] = relationship(back_populates="employee_assignments")


class AccountSession(AuthBase):
    __tablename__ = "AccountSessions"
    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_AccountSessions_token_hash"),
        CheckConstraint("csrf_token_hash IS NULL OR csrf_token_hash ~ '^[0-9a-f]{64}$'",
                        name="ck_AccountSessions_csrf_hash"),
        CheckConstraint("auth_version >= 0", name="ck_AccountSessions_auth_version"),
        CheckConstraint("created_at <= last_activity_at AND last_activity_at < idle_expires_at "
                        "AND idle_expires_at <= expires_at", name="ck_AccountSessions_expiration"),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="ck_AccountSessions_revocation"),
        CheckConstraint("revoked_at IS NOT NULL OR revocation_reason IS NULL",
                        name="ck_AccountSessions_revocation_reason"),
        Index("ix_AccountSessions_account_id", "account_id"),
        Index("ix_AccountSessions_expires_at", "expires_at"),
    )

    session_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("PersonalAccounts.account_id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Reservado para fase 2: nunca persistir tampoco el token CSRF en claro.
    csrf_token_hash: Mapped[str | None] = mapped_column(String(64))
    auth_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(50))

    account: Mapped[PersonalAccount] = relationship(back_populates="sessions")


class AuditEvent(AuthBase):
    __tablename__ = "AuditEvents"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="ck_AuditEvents_outcome"),
        CheckConstraint("action ~ '^[a-z][a-z0-9_.:]{0,99}$'", name="ck_AuditEvents_action"),
        Index("ix_AuditEvents_actor_created", "actor_account_id", "created_at"),
    )

    audit_event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_account_id: Mapped[int | None] = mapped_column(ForeignKey("PersonalAccounts.account_id"))
    action: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(20))
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100))
    request_id: Mapped[UUID | None] = mapped_column(Uuid)
    failure_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor: Mapped[PersonalAccount | None] = relationship(back_populates="audit_events")

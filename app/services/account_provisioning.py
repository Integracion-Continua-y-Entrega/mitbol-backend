"""Operador fuera de HTTP: altas y cambios de acceso auditados, sin commits internos."""

from datetime import date
import re
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, text

from app.core.account_identity import normalize_email
from app.core.passwords import hash_password
from app.models import AuditEvent, Employee, EmployeeRole, Person, PersonalAccount, Role
from app.repositories import auth as repo


class AccountData(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=1, max_length=20)
    email: str
    employee_code: str = Field(min_length=1, max_length=30)
    hired_at: date
    active: bool
    roles: tuple[str, ...]

    @field_validator("email")
    @classmethod
    def email_normalized(cls, value):
        return normalize_email(value)

    @field_validator("roles")
    @classmethod
    def known_roles(cls, value):
        if not value or set(value) - {"admin", "staff"}:
            raise ValueError("Se requiere Admin y/o Staff explícito.")
        return tuple(sorted(set(value)))


def operator_identity(db):
    effective, login = db.execute(text("SELECT current_user, session_user")).one()
    if (effective != login or effective in {"mitbol_admin_writer", "mitbol_render_reader"}
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", effective)):
        raise ValueError("Se requiere una conexión independiente de operador autorizado.")
    return effective


def roles_for(db, codes):
    codes = tuple(sorted(set(codes)))
    if not codes or set(codes) - {"admin", "staff"}:
        raise ValueError("Roles no admitidos.")
    rows = list(db.scalars(select(Role).where(Role.code.in_(codes))))
    if len(rows) != len(codes):
        raise ValueError("Primero debe existir el catálogo de roles aprobado.")
    return rows


def audit_operator(db, operator, action, account_id):
    request_id = uuid4()
    # Primer alta sin actor de aplicación: acción identifica el login SQL real.
    # El operador usa una identidad controlada; nunca se acepta como dato HTTP.
    db.add(AuditEvent(actor_account_id=None, action=f"operator.{operator}.{action}", outcome="success",
                      resource_type="account", resource_id=str(account_id), request_id=request_id))
    return request_id


def create_account(db, data: AccountData, password):
    operator = operator_identity(db)
    roles = roles_for(db, data.roles)
    encoded = hash_password(password)
    person = Person(name=data.name, phone=data.phone)
    account = PersonalAccount(person=person, email=data.email, password_hash=encoded, is_active=data.active)
    employee = Employee(person=person, employee_code=data.employee_code, hired_at=data.hired_at, is_active=data.active)
    db.add_all([account, employee])
    db.flush()
    db.add_all(EmployeeRole(person_id=person.person_id, role_id=role.role_id) for role in roles)
    request_id = audit_operator(db, operator, "account.create", account.account_id)
    db.flush()
    return account.account_id, request_id


def change_access(db, account_id, *, account_active: bool, employee_active: bool, role_codes):
    operator = operator_identity(db)
    if type(account_active) is not bool or type(employee_active) is not bool:
        raise ValueError("La activación debe ser explícita.")
    account = repo.lock_account(db, account_id)
    if not account:
        raise ValueError("Cuenta no encontrada.")
    employee = db.get(Employee, account.person_id)
    if not employee:
        raise ValueError("Perfil laboral no encontrado.")
    roles = roles_for(db, role_codes)
    account.is_active, employee.is_active = account_active, employee_active
    db.execute(delete(EmployeeRole).where(EmployeeRole.person_id == account.person_id))
    db.add_all(EmployeeRole(person_id=account.person_id, role_id=role.role_id) for role in roles)
    now = db.scalar(select(func.clock_timestamp()))
    account.updated_at = now
    repo.invalidate_locked_account(db, account, now, "operator_access_change")
    request_id = audit_operator(db, operator, "access.change", account_id)
    db.flush()
    return account_id, request_id

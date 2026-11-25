"""Emisión, consulta y consumo interno de QR; no registra visitas ni concede recompensas."""

from datetime import timedelta
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.auth_errors import AuthError, invalid_session
from app.core.loyalty_identification import generate_identification, hash_identification
from app.core.session_policy import session_is_valid, utc_datetime
from app.models import AccountSession, Employee, LoyaltyIdentification, Person, PersonalAccount
from app.schemas.loyalty_identification import LoyaltyIdentificationInput, LoyaltyIdentificationRead, LoyaltyIdentificationValidation


IDENTIFICATION_LIFETIME = timedelta(minutes=5)


def _require_schema(db):
    if not db.scalar(text("""SELECT to_regclass('public."LoyaltyIdentifications"') IS NOT NULL""")):
        raise AuthError(503, "La identificación de fidelización requiere la migración 0017.", code="LOYALTY_IDENTIFICATION_UNAVAILABLE")


def _invalid():
    return AuthError(409, "La identificación no es válida o ya no está disponible; solicita un código nuevo.",
                     code="LOYALTY_IDENTIFICATION_INVALID")


def _digest(payload):
    try:
        parsed = LoyaltyIdentificationInput.model_validate(payload)
        return hash_identification(parsed.qr_payload)
    except (ValidationError, ValueError):
        raise AuthError(422, "Identificación de fidelización inválida.", code="LOYALTY_IDENTIFICATION_INPUT") from None


def issue_identification(context, *, clock) -> LoyaltyIdentificationRead:
    """Usa la cuenta y sesión bloqueadas por autenticación; renovar reemplaza la credencial actual."""
    db = context.db
    _require_schema(db)
    db.scalar(select(LoyaltyIdentification.account_id).where(LoyaltyIdentification.account_id == context.account.account_id)
              .with_for_update())
    now = utc_datetime(clock())
    if (context.session.account_id != context.account.account_id
            or not session_is_valid(context.session, account_active=context.account.is_active,
                                    current_auth_version=context.account.auth_version, now=now)):
        raise invalid_session()
    expires = min(now + IDENTIFICATION_LIFETIME, context.session.idle_expires_at, context.session.expires_at)
    token = generate_identification()
    values = {"account_id": context.account.account_id, "session_id": context.session.session_id,
              "token_hash": token.digest, "auth_version": context.account.auth_version,
              "created_at": now, "expires_at": expires}
    renewed = {name: value for name, value in values.items() if name != "account_id"} | {
        "consumed_at": None, "consumed_by_account_id": None}
    db.execute(insert(LoyaltyIdentification).values(**values).on_conflict_do_update(
        index_elements=[LoyaltyIdentification.account_id], set_=renewed))
    return LoyaltyIdentificationRead(qr_payload=token.qr_payload, issued_at=now, expires_at=expires)


def _lookup(db, digest):
    """Lee un estado actual sin bloquear la cuenta ajena ni generar ciclos entre empleados."""
    return db.execute(select(
        LoyaltyIdentification.account_id, LoyaltyIdentification.created_at, LoyaltyIdentification.expires_at,
        LoyaltyIdentification.consumed_at, LoyaltyIdentification.auth_version.label("identification_version"),
        AccountSession.auth_version.label("session_version"), AccountSession.created_at.label("session_created"),
        AccountSession.last_activity_at, AccountSession.idle_expires_at, AccountSession.expires_at.label("session_expires"),
        AccountSession.revoked_at, PersonalAccount.is_active.label("account_active"),
        PersonalAccount.auth_version.label("account_version"), Person.name, Employee.is_active.label("employee_active"),
    ).select_from(LoyaltyIdentification).join(AccountSession, (AccountSession.session_id == LoyaltyIdentification.session_id)
           & (AccountSession.account_id == LoyaltyIdentification.account_id))
      .join(PersonalAccount, PersonalAccount.account_id == LoyaltyIdentification.account_id)
      .join(Person, Person.person_id == PersonalAccount.person_id)
      .outerjoin(Employee, Employee.person_id == Person.person_id)
      .where(LoyaltyIdentification.token_hash == digest)).one_or_none()


def _verified(row, now):
    if row is None:
        raise _invalid()
    session = SimpleNamespace(auth_version=row.session_version, created_at=row.session_created,
        last_activity_at=row.last_activity_at, idle_expires_at=row.idle_expires_at,
        expires_at=row.session_expires, revoked_at=row.revoked_at)
    if (row.consumed_at is not None or not row.created_at <= now < row.expires_at
            or row.identification_version != row.account_version
            or not session_is_valid(session, account_active=row.account_active and row.employee_active is not False,
                                    current_auth_version=row.account_version, now=now)):
        raise _invalid()
    return LoyaltyIdentificationValidation(account_id=row.account_id, name=row.name, expires_at=row.expires_at)


def validate_identification(db: Session, payload: LoyaltyIdentificationInput, *, clock) -> LoyaltyIdentificationValidation:
    """La revisión del personal es de solo lectura; no registra ni consume una visita."""
    digest = _digest(payload)
    _require_schema(db)
    row = _lookup(db, digest)
    return _verified(row, utc_datetime(clock()))


def consume_identification(db: Session, payload: LoyaltyIdentificationInput, *, actor_account_id: int, clock) -> LoyaltyIdentificationValidation:
    """Reserva el uso único dentro de la transacción que HU-043 deberá compartir con el registro de visita.

    Bloquea solo la credencial del cliente, sin tomar bloqueos de su cuenta o
    sesión. El llamador decide commit o rollback; no hay endpoint de consumo aislado.
    """
    digest = _digest(payload)
    _require_schema(db)
    identifier = db.scalar(select(LoyaltyIdentification.account_id).where(LoyaltyIdentification.token_hash == digest).with_for_update())
    if identifier is None:
        raise _invalid()
    row = _lookup(db, digest)
    now = utc_datetime(clock())
    result = _verified(row, now)
    consumed = db.scalar(update(LoyaltyIdentification).where(LoyaltyIdentification.account_id == identifier,
        LoyaltyIdentification.token_hash == digest, LoyaltyIdentification.consumed_at.is_(None))
        .values(consumed_at=now, consumed_by_account_id=actor_account_id).returning(LoyaltyIdentification.account_id)
        .execution_options(synchronize_session=False))
    if consumed is None:
        raise _invalid()
    return result

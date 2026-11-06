"""Orden de locks: cuenta, sesión. Los cambios de acceso bloquean la cuenta."""

from sqlalchemy import select, update

from app.models import AccountSession, Employee, EmployeeRole, PersonalAccount, Role


def account_for_email(db, email):
    return db.scalar(select(PersonalAccount).where(PersonalAccount.email == email).with_for_update())


def lock_account(db, account_id):
    return db.scalar(select(PersonalAccount).where(PersonalAccount.account_id == account_id)
                     .with_for_update().execution_options(populate_existing=True))


def employee_and_roles(db, person_id):
    # El llamador ya mantiene lock_account/account_for_email. Toda herramienta
    # autorizada de cambio de empleado/asignaciones debe tomar ese mismo lock,
    # actualizar auth_version y revocar en su transacción. Roles.code es catálogo
    # de seguridad inmutable para la identidad web, no una edición concurrente.
    # FOR UPDATE aquí exigiría UPDATE sobre EmployeeRoles al usuario web.
    employee = db.scalar(select(Employee).where(Employee.person_id == person_id))
    roles = db.scalars(select(Role).join(EmployeeRole).where(EmployeeRole.person_id == person_id)
                       .order_by(Role.role_id)).all() if employee else []
    return employee, roles


def account_id_for_token(db, digest):
    return db.scalar(select(AccountSession.account_id).where(AccountSession.token_hash == digest))


def lock_session(db, digest):
    return db.scalar(select(AccountSession).where(AccountSession.token_hash == digest).with_for_update()
                     .execution_options(populate_existing=True))


def revoke_all(db, account_id, now, reason):
    db.execute(update(AccountSession).where(AccountSession.account_id == account_id,
                                            AccountSession.revoked_at.is_(None)).values(
        revoked_at=now, revocation_reason=reason,
    ).execution_options(synchronize_session=False))


def invalidate_locked_account(db, account, now, reason):
    """Sólo bajo lock_account: usar también al cambiar roles/estado autorizado."""
    account.auth_version += 1
    revoke_all(db, account.account_id, now, reason)

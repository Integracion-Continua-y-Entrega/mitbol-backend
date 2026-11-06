"""Sesiones administrativas locales. El llamador nunca recibe hashes de cuenta."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from secrets import compare_digest, token_urlsafe
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth_errors import AuthError, invalid_session
from app.core.auth_limiter import LocalAttemptLimiter, PasswordWorkGate
from app.core.passwords import hash_password, verify_password
from app.core.permissions import permissions_for_roles
from app.core.session_policy import SessionPolicy, session_is_valid, utc_datetime
from app.core.session_tokens import generate_session_token, hash_session_token
from app.db.connection import DatabaseTargetError, validate_local_connection
from app.models import AccountSession, AuditEvent, Person
from app.repositories import auth as repo
from app.services.admin_transaction import recover_image_storages


@dataclass(frozen=True)
class Principal:
    account_id: int
    person_id: int
    email: str
    name: str
    roles: tuple[str, ...]
    permissions: frozenset[str]


@dataclass
class AuthContext:
    principal: Principal
    account: object = field(repr=False)
    session: AccountSession = field(repr=False)
    db: object = field(repr=False)
    csrf: str = field(repr=False)
    request_id: object
    session_id: object
    closed: bool = False


@dataclass(frozen=True)
class IssuedSession:
    expires_at: datetime
    idle_expires_at: datetime


def csrf_for_context(token, account, employee, roles, permissions):
    # La clave es el secreto de sesión; el contexto liga la sesión a la seguridad
    # vigente. Un cambio observado invalida el hash emitido sin añadir columnas.
    context = json.dumps([
        "mitbol:csrf:v1", account.account_id, account.person_id, account.email,
        account.password_hash, account.auth_version, account.is_active,
        employee.is_active if employee else False,
        sorted((role.role_id, role.code) for role in roles), sorted(permissions),
    ], separators=(",", ":"), ensure_ascii=True)
    return hmac.new(token.encode("ascii"), context.encode("utf-8"), sha256).hexdigest()


class AuthService:
    def __init__(self, session_factory, config, *, clock=None):
        self.sessions = session_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.policy = SessionPolicy(timedelta(seconds=config.auth_absolute_seconds),
                                    timedelta(seconds=config.auth_idle_seconds))
        self.touch_interval = timedelta(seconds=config.auth_touch_seconds)
        self.limiter = LocalAttemptLimiter(account_limit=config.auth_login_attempts,
                                          ip_limit=config.auth_ip_attempts,
                                          window_seconds=config.auth_limit_window_seconds,
                                          max_keys=config.auth_limit_max_keys)
        if config.auth_limit_backend == "postgresql":
            from app.services.persistent_auth_limiter import PostgresAttemptLimiter
            self.limiter = PostgresAttemptLimiter(self.transaction, config)
        self.password_work = PasswordWorkGate(config.auth_hash_concurrency)
        self._dummy_hash = hash_password(token_urlsafe(32))

    def now(self):
        return utc_datetime(self.clock())

    @contextmanager
    def transaction(self):
        try:
            with self.sessions() as db, db.begin():
                validate_local_connection(db.connection())
                connection = db.connection()
                identity = db.execute(text("SELECT current_database(), current_user, session_user")).one()
                if identity != (connection.engine.url.database, connection.engine.url.username, connection.engine.url.username):
                    raise DatabaseTargetError("Identidad local de autenticación inesperada.")
                yield db
        except (SQLAlchemyError, DatabaseTargetError):
            raise AuthError(503, "La autenticación no está disponible temporalmente.", code="AUTH_UNAVAILABLE") from None

    @staticmethod
    def audit(db, action, outcome, request_id, *, actor_id=None, session_id=None, code=None):
        db.add(AuditEvent(actor_account_id=actor_id, action=action, outcome=outcome,
                          request_id=request_id, resource_type="session" if session_id else None,
                          resource_id=str(session_id) if session_id else None, failure_code=code))

    @staticmethod
    def principal(db, account, roles, permissions):
        name = db.scalar(select(Person.name).where(Person.person_id == account.person_id))
        return Principal(account.account_id, account.person_id, account.email, name,
                         tuple(sorted(role.code for role in roles)), permissions)

    def login(self, email, password, *, ip, request_id, old_token=None):
        self.limiter.admit(email, ip)
        denied = False
        with self.transaction() as db:
            account = repo.account_for_email(db, email)
            employee, roles = repo.employee_and_roles(db, account.person_id) if account else (None, [])
            with self.password_work.acquire():
                correct = verify_password(password, account.password_hash if account else self._dummy_hash)
            if not correct or not account or not account.is_active or not employee or not employee.is_active:
                self.audit(db, "auth.login", "denied", request_id, code="INVALID_CREDENTIALS")
                denied = True
            else:
                now = self.now()
                permissions = permissions_for_roles((role.code for role in roles), employee_active=True)
                token = generate_session_token()
                csrf = csrf_for_context(token.value, account, employee, roles, permissions)
                expires, idle = self.policy.deadlines(now)
                row = AccountSession(session_id=uuid4(), account_id=account.account_id, token_hash=token.digest,
                                     csrf_token_hash=sha256(csrf.encode()).hexdigest(), auth_version=account.auth_version,
                                     created_at=now, last_activity_at=now, expires_at=expires, idle_expires_at=idle)
                # Rotar la sesión del mismo navegador/cuenta, sin locks cruzados entre cuentas.
                try:
                    old_digest = hash_session_token(old_token)
                except ValueError:
                    old_digest = None
                if old_digest:
                    db.execute(update(AccountSession).where(AccountSession.token_hash == old_digest,
                        AccountSession.account_id == account.account_id, AccountSession.revoked_at.is_(None)
                    ).values(revoked_at=now, revocation_reason="login_rotation"))
                db.add(row)
                account.last_login_at = now
                principal = self.principal(db, account, roles, permissions)
                self.audit(db, "auth.login", "success", request_id, actor_id=account.account_id, session_id=row.session_id)
                issued = IssuedSession(expires, idle)
        if denied:
            raise AuthError(401, "Credenciales inválidas.", code="INVALID_CREDENTIALS")
        return principal, issued, token, csrf

    def _resolve(self, db, token, request_id):
        try:
            digest = hash_session_token(token)
        except ValueError:
            raise invalid_session() from None
        account_id = repo.account_id_for_token(db, digest)
        account = repo.lock_account(db, account_id) if account_id is not None else None
        employee, roles = repo.employee_and_roles(db, account.person_id) if account else (None, [])
        row = repo.lock_session(db, digest) if account else None
        now = self.now()
        permissions = permissions_for_roles((role.code for role in roles), employee_active=bool(employee and employee.is_active))
        valid = bool(row and account and employee and employee.is_active and session_is_valid(
            row, account_active=account.is_active, current_auth_version=account.auth_version, now=now))
        csrf = csrf_for_context(token, account, employee, roles, permissions) if valid else ""
        valid = valid and bool(row.csrf_token_hash and compare_digest(sha256(csrf.encode()).hexdigest(), row.csrf_token_hash))
        if not valid:
            error = invalid_session()
            error.actor_id, error.session_id = account_id, row.session_id if row else None
            error.revoke = row is not None
            raise error
        return AuthContext(self.principal(db, account, roles, permissions), account, row, db, csrf, request_id, row.session_id)

    @contextmanager
    def authenticated(self, token, *, request_id, csrf_header=None, require_csrf=False):
        try:
            hash_session_token(token)
        except ValueError:
            raise invalid_session() from None
        context = None
        try:
            with self.transaction() as db:
                context = self._resolve(db, token, request_id)
                db.info["auth_context"] = context
                if require_csrf and (not isinstance(csrf_header, str) or len(csrf_header) != 64
                                     or not csrf_header.isascii() or not compare_digest(csrf_header, context.csrf)):
                    raise AuthError(403, "Verificación CSRF inválida.", code="CSRF_REJECTED")
                now = self.now()
                if now - context.session.last_activity_at >= self.touch_interval:
                    context.session.last_activity_at = now
                    context.session.idle_expires_at = self.policy.next_idle_expiry(now, context.session.expires_at)
                yield context
                if not context.closed:
                    now = self.now()
                    if not session_is_valid(context.session, account_active=context.account.is_active,
                                            current_auth_version=context.account.auth_version, now=now):
                        error = invalid_session()
                        error.revoke = True
                        raise error
        except AuthError as error:
            if context:
                error.actor_id, error.session_id = context.principal.account_id, context.session_id
            if error.actor_id is not None and error.status_code != 503:
                with self.transaction() as db:
                    repo.lock_account(db, error.actor_id)
                    if error.revoke and error.session_id:
                        row = db.scalar(select(AccountSession).where(AccountSession.session_id == error.session_id).with_for_update())
                        if row and row.revoked_at is None:
                            row.revoked_at = max(self.now(), row.created_at)
                            row.revocation_reason = "invalid_security_state"
                    self.audit(db, "auth.access", "denied", request_id, actor_id=error.actor_id,
                               session_id=error.session_id, code=error.code)
            raise
        finally:
            if context is not None:
                recover_image_storages(context.db, self.transaction)

    def logout(self, context, *, all_sessions=False):
        now = self.now()
        if all_sessions:
            repo.invalidate_locked_account(context.db, context.account, now, "logout_all")
        else:
            context.session.revoked_at, context.session.revocation_reason = now, "logout"
        context.closed = True
        self.audit(context.db, "auth.logout_all" if all_sessions else "auth.logout", "success", context.request_id,
                   actor_id=context.account.account_id, session_id=context.session.session_id)

    def change_password(self, context, current_password, new_password, *, ip):
        self.limiter.admit(context.account.email, ip, operation="change_password")
        with self.password_work.acquire():
            if not verify_password(current_password, context.account.password_hash):
                raise AuthError(400, "No fue posible cambiar la contraseña.", code="INVALID_CURRENT_PASSWORD")
            if current_password == new_password:
                raise AuthError(422, "La nueva contraseña debe ser distinta.", code="PASSWORD_REUSE")
            new_hash = hash_password(new_password)
        now = self.now()
        context.account.password_hash = new_hash
        context.account.updated_at = now
        repo.invalidate_locked_account(context.db, context.account, now, "password_changed")
        context.closed = True
        self.audit(context.db, "auth.change_password", "success", context.request_id,
                   actor_id=context.account.account_id, session_id=context.session.session_id)

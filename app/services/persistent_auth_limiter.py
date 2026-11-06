"""Admisión compartida en transacción independiente, anterior a Argon2/login."""

from datetime import timedelta
from hashlib import sha256
import hmac
from ipaddress import ip_address, ip_network
import json
import math

from sqlalchemy import delete, func, select

from app.core.account_identity import normalize_email
from app.core.auth_errors import AuthError
from app.models import AuthRateLimit, AuthRateLimitPolicy

LIMIT_LOCK_KEY = 0x4D4954424F4C524C


class PostgresAttemptLimiter:
    def __init__(self, transaction, config):
        self.transaction = transaction
        self._key = config.auth_limit_secret.get_secret_value().encode("ascii")
        self.account_limit, self.ip_limit = config.auth_login_attempts, config.auth_ip_attempts
        self.window_seconds, self.max_keys = config.auth_limit_window_seconds, config.auth_limit_max_keys
        policy = json.dumps(["mitbol:rate-policy:v1", self.account_limit, self.ip_limit,
                             self.window_seconds, self.max_keys], separators=(",", ":"))
        self._fingerprint = hmac.new(self._key, policy.encode(), sha256).hexdigest()

    def _digest(self, operation, kind, value):
        return hmac.new(self._key, json.dumps(["mitbol:rate-key:v1", operation, kind, value],
                        separators=(",", ":")).encode(), sha256).hexdigest()

    def admit(self, identity, ip, *, operation="login"):
        if operation not in {"login", "change_password"}:
            raise ValueError("Operación de autenticación no admitida.")
        identity = normalize_email(identity)
        address = ip_address(ip)
        network = str(ip_network(f"{address}/64", strict=False)) if address.version == 6 else str(address)
        keys = [(self._digest(operation, "account", identity), self.account_limit),
                (self._digest(operation, "network", network), self.ip_limit)]
        denied = None
        with self.transaction() as db:
            if not db.scalar(select(func.pg_try_advisory_xact_lock(LIMIT_LOCK_KEY))):
                raise AuthError(429, "Demasiados intentos; intenta más tarde.", code="LIMIT_BUSY", retry_after=1)
            now = db.scalar(select(func.clock_timestamp()))
            policy = db.get(AuthRateLimitPolicy, 1)
            if policy is None:
                db.add(AuthRateLimitPolicy(policy_id=1, fingerprint=self._fingerprint))
            elif not hmac.compare_digest(policy.fingerprint, self._fingerprint):
                raise AuthError(503, "La configuración compartida de límites no coincide.", code="LIMIT_POLICY_MISMATCH")
            db.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= now))
            rows = {row.key_hash: row for row in db.scalars(select(AuthRateLimit).where(
                AuthRateLimit.key_hash.in_([key for key, _ in keys])))}
            retry = max((math.ceil((rows[key].expires_at - now).total_seconds())
                         for key, limit in keys if key in rows and rows[key].attempts >= limit), default=0)
            count = db.scalar(select(func.count()).select_from(AuthRateLimit))
            if count + sum(key not in rows for key, _ in keys) > self.max_keys:
                retry = max(retry, self.window_seconds)
            if retry:
                denied = AuthError(429, "Demasiados intentos; intenta más tarde.", code="RATE_LIMITED", retry_after=max(1, retry))
            else:
                for key, _ in keys:
                    if key in rows:
                        rows[key].attempts += 1
                    else:
                        db.add(AuthRateLimit(key_hash=key, attempts=1, started_at=now,
                                             expires_at=now + timedelta(seconds=self.window_seconds)))
        # La admisión se confirma aunque falle luego contraseña, auditoría o hash gate.
        if denied:
            raise denied

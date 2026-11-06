"""Tiempos y validez de sesiones; primitivas puras, todavía sin login HTTP."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Las fechas de sesión deben incluir zona horaria.")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class SessionPolicy:
    absolute_lifetime: timedelta = timedelta(hours=8)
    idle_timeout: timedelta = timedelta(minutes=30)

    def __post_init__(self):
        if not timedelta(0) < self.idle_timeout <= self.absolute_lifetime:
            raise ValueError("La inactividad debe ser positiva y no superar la duración absoluta.")

    def deadlines(self, now: datetime) -> tuple[datetime, datetime]:
        now = utc_datetime(now)
        return now + self.absolute_lifetime, now + self.idle_timeout

    def next_idle_expiry(self, now: datetime, expires_at: datetime) -> datetime:
        return min(utc_datetime(now) + self.idle_timeout, utc_datetime(expires_at))


class SessionState(Protocol):
    auth_version: int
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime
    idle_expires_at: datetime
    revoked_at: datetime | None


def session_is_valid(state: SessionState, *, account_active: bool,
                     current_auth_version: int, now: datetime) -> bool:
    now = utc_datetime(now)
    return bool(account_active and state.revoked_at is None
                and current_auth_version >= 0 and state.auth_version == current_auth_version
                and utc_datetime(state.created_at) <= utc_datetime(state.last_activity_at) <= now
                and now < utc_datetime(state.idle_expires_at) <= utc_datetime(state.expires_at))

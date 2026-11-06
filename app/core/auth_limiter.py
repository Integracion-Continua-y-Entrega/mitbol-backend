"""Sólo un proceso local: memoria acotada, ventana fija y operaciones atómicas."""

from contextlib import contextmanager
from hashlib import sha256
import hmac
import math
from secrets import token_bytes
from threading import BoundedSemaphore, Lock
import time

from app.core.auth_errors import AuthError


class LocalAttemptLimiter:
    def __init__(self, *, account_limit=5, ip_limit=30, window_seconds=900, max_keys=2000, clock=time.monotonic):
        self.account_limit, self.ip_limit = account_limit, ip_limit
        self.window_seconds, self.max_keys, self.clock = window_seconds, max_keys, clock
        self._entries = {}
        self._key = token_bytes(32)
        self._lock = Lock()

    def admit(self, identity: str, ip: str, *, operation="login") -> None:
        now = self.clock()
        def key(kind, value):
            return hmac.new(self._key, f"{operation}:{kind}:{value}".encode(), sha256).digest()
        keys = [(key("account", identity), self.account_limit), (key("ip", ip), self.ip_limit)]
        with self._lock:
            expired = [key for key, (start, _) in self._entries.items() if now - start >= self.window_seconds]
            for item in expired:
                del self._entries[item]
            retry = 0
            for item, limit in keys:
                start, count = self._entries.get(item, (now, 0))
                if count >= limit:
                    retry = max(retry, math.ceil(start + self.window_seconds - now))
            if len(self._entries) + sum(item not in self._entries for item, _ in keys) > self.max_keys:
                retry = max(retry, self.window_seconds)
            if retry:
                raise AuthError(429, "Demasiados intentos; intenta más tarde.", code="RATE_LIMITED", retry_after=max(1, retry))
            for item, _ in keys:
                start, count = self._entries.get(item, (now, 0))
                self._entries[item] = (start, count + 1)


class PasswordWorkGate:
    def __init__(self, limit=2):
        self._slots = BoundedSemaphore(limit)

    @contextmanager
    def acquire(self):
        if not self._slots.acquire(blocking=False):
            raise AuthError(429, "Demasiados intentos; intenta más tarde.", code="HASH_BUSY", retry_after=1)
        try:
            yield
        finally:
            self._slots.release()

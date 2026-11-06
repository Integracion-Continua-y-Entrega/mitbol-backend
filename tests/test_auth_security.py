"""Primitivas de seguridad sin HTTP, secretos reales o conexiones."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from argon2 import PasswordHasher, Type, extract_parameters
import pytest

from app.core.account_identity import normalize_email
from app.core.passwords import hash_password, password_needs_rehash, verify_password
from app.core.permissions import permissions_for_roles
from app.core.session_policy import SessionPolicy, session_is_valid
from app.core.session_tokens import generate_session_token, hash_session_token, session_token_matches


@pytest.fixture(scope="module")
def password_sample():
    password = "Frase sintética de prueba con espacios "
    return password, hash_password(password)


def test_argon2id_salt_and_parameters_and_password_not_trimmed(password_sample):
    password, encoded = password_sample
    params = extract_parameters(encoded)
    assert params.type == Type.ID and params.memory_cost >= 65_536 and params.time_cost >= 3
    assert password not in encoded and encoded != hash_password(password)
    assert verify_password(password, encoded)
    assert not verify_password(password.rstrip(), encoded)
    assert not verify_password("otra frase suficientemente larga", encoded)
    assert not password_needs_rehash(encoded)


@pytest.mark.parametrize("password", [None, b"not-a-string", "short", "x" * 1025])
def test_hash_rejects_invalid_password_without_echoing_input(password):
    with pytest.raises(ValueError, match="entre 15 y 1024"):
        hash_password(password)


@pytest.mark.parametrize("encoded", [None, "plaintext", "$argon2id$malformed", "$argon2i$malformed"])
def test_invalid_hashes_fail_closed(password_sample, encoded):
    assert not verify_password(password_sample[0], encoded)
    assert password_needs_rehash(encoded)


def test_parameter_upgrade_detected_and_unicode_preserved():
    password = "🔐 Contraseña sintética üñîçødé "
    old_hash = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1, type=Type.ID).hash(password)
    assert verify_password(password, old_hash) and password_needs_rehash(old_hash)
    assert verify_password(password, hash_password(password))


@pytest.mark.parametrize("value,expected", [
    ("  ADMIN@Example.COM  ", "admin@example.com"),
    ("Persona+Admin@Example.com", "persona+admin@example.com"),
    ("a.b@example.com", "a.b@example.com"),
])
def test_email_canonicalization_preserves_provider_independent_identity(value, expected):
    assert normalize_email(value) == expected


@pytest.mark.parametrize("value", [None, "", "no-at", "a@@b", "a@", "@b", "a b@example.com", "a\x00@b"])
def test_email_invalid(value):
    with pytest.raises(ValueError):
        normalize_email(value)


@pytest.mark.parametrize("roles,active,expected", [
    (["staff"], True, {"catalog:read", "availability:write"}),
    (["admin"], True, {"catalog:read", "description:write", "availability:write", "images:write", "tags:write", "settings:write"}),
    (["admin", "staff"], False, set()), ([], True, set()),
    (["neondb_owner", "mitbol_render_reader", "ADMIN", "unknown"], True, set()),
])
def test_roles_are_explicit_and_employment_required(roles, active, expected):
    assert permissions_for_roles(roles, employee_active=active) == expected


def test_combined_roles_do_not_escalate_beyond_admin():
    assert permissions_for_roles(["admin", "staff", "unknown"], employee_active=True) == (
        permissions_for_roles(["admin"], employee_active=True)
    )


def test_session_tokens_are_distinct_canonical_and_hidden():
    first, second = generate_session_token(), generate_session_token()
    assert len(first.value) == 43 and len(first.digest) == 64
    assert first.value != second.value and first.digest != second.digest
    assert hash_session_token(first.value) == first.digest
    assert first.value not in repr(first) and first.digest not in repr(first)
    assert session_token_matches(first.value, first.digest)
    assert not session_token_matches(second.value, first.digest)


@pytest.mark.parametrize("value", [None, "", "x" * 42, "x" * 44, "é" * 43, "a" * 42 + "B"])
def test_malformed_session_tokens_fail_closed(value):
    with pytest.raises(ValueError):
        hash_session_token(value)
    assert not session_token_matches(value, "0" * 64)


@pytest.mark.parametrize("digest", [None, "é" * 64, "", "A" * 64])
def test_malformed_token_digest_fails_closed(digest):
    assert not session_token_matches(generate_session_token().value, digest)


NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def state(**changes):
    values = dict(auth_version=0, created_at=NOW, last_activity_at=NOW,
                  expires_at=NOW + timedelta(hours=8), idle_expires_at=NOW + timedelta(minutes=30),
                  revoked_at=None)
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("active,version,offset,changes,expected", [
    (True, 0, timedelta(), {}, True),
    (False, 0, timedelta(), {}, False),
    (True, 1, timedelta(), {}, False),
    (True, 0, timedelta(minutes=30), {}, False),
    (True, 0, timedelta(hours=8), {"idle_expires_at": NOW + timedelta(hours=8)}, False),
    (True, 0, timedelta(), {"revoked_at": NOW}, False),
    (True, 0, timedelta(seconds=-1), {}, False),
])
def test_session_validity_boundaries_and_revocation(active, version, offset, changes, expected):
    assert session_is_valid(state(**changes), account_active=active,
                            current_auth_version=version, now=NOW + offset) is expected


def test_session_policy_is_configurable_and_never_extends_absolute_expiry():
    policy = SessionPolicy()
    assert policy.deadlines(NOW) == (NOW + timedelta(hours=8), NOW + timedelta(minutes=30))
    end = NOW + timedelta(hours=8)
    assert policy.next_idle_expiry(end - timedelta(minutes=1), end) == end
    custom = SessionPolicy(absolute_lifetime=timedelta(hours=1), idle_timeout=timedelta(minutes=5))
    assert custom.deadlines(NOW) == (NOW + timedelta(hours=1), NOW + timedelta(minutes=5))


@pytest.mark.parametrize("absolute,idle", [(0, 1), (1, 0), (1, 2), (-1, 1)])
def test_invalid_session_policy_rejected(absolute, idle):
    with pytest.raises(ValueError):
        SessionPolicy(timedelta(minutes=absolute), timedelta(minutes=idle))


def test_timezone_is_required_and_offsets_compare_as_utc():
    with pytest.raises(ValueError, match="zona horaria"):
        SessionPolicy().deadlines(NOW.replace(tzinfo=None))
    mexico = timezone(timedelta(hours=-6))
    assert SessionPolicy().deadlines(NOW.astimezone(mexico)) == SessionPolicy().deadlines(NOW)

"""HU-042: material QR separado de sesiones y persistencia privada, sin visitas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from pydantic import ValidationError
import pytest
from sqlalchemy import delete, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.loyalty_identification import QR_PREFIX, QR_VALUE_LENGTH, generate_identification, hash_identification
from app.core.session_tokens import generate_session_token, hash_session_token
from app.db.schema_compatibility import validate_bridge_schema
from app.models import (
    AccountSession, AuthBase, Base, CommercialDiscountBase, CouponBase, LoyaltyBase,
    LoyaltyIdentification, LoyaltyIdentificationBase, OrderBase, OrderPaymentBase, OrderStateBase,
    PasswordRecoveryBase, PersonalAccount,
)
from app.models.sync_run import SyncBase
from app.schemas.loyalty_identification import LoyaltyIdentificationInput, LoyaltyIdentificationRead
from test_auth_api import auth_database, backend as auth_backend, login, password_hash
from test_auth_persistence import config_for


REVISION = "20261124_0017"
PREVIOUS = "20261124_0016"


@pytest.fixture
def backend(auth_backend):
    b = auth_backend
    with b.sessions.kw["bind"].begin() as db:
        command.upgrade(config_for(db), REVISION)
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(LoyaltyIdentification))


def emitter_session(b, role="customer"):
    assert login(b, role).status_code == 200
    with b.sessions() as db:
        return db.scalar(select(AccountSession).where(AccountSession.account_id == b.ids[role][0])
                         .order_by(AccountSession.created_at.desc()))


def record(b, session, **changes):
    return LoyaltyIdentification(**({"account_id": session.account_id, "session_id": session.session_id,
        "token_hash": generate_identification().digest, "auth_version": session.auth_version,
        "created_at": b.now.value, "expires_at": b.now.value + timedelta(minutes=5)} | changes))


def test_generated_payload_is_opaque_canonical_unique_and_hidden_from_repr():
    values = [generate_identification() for _ in range(20)]
    assert len({row.qr_payload for row in values}) == 20
    assert len({row.digest for row in values}) == 20
    for row in values:
        assert row.qr_payload.startswith(QR_PREFIX) and len(row.qr_payload) == QR_VALUE_LENGTH
        assert hash_identification(row.qr_payload) == row.digest
        assert row.qr_payload not in repr(row) and row.digest not in repr(row)
        assert "http" not in row.qr_payload and "@" not in row.qr_payload


def test_identification_and_session_tokens_are_not_interchangeable():
    session = generate_session_token()
    payload = QR_PREFIX + session.value
    assert hash_identification(payload) != session.digest
    with pytest.raises(ValueError):
        hash_identification(session.value)
    with pytest.raises(ValueError):
        hash_session_token(payload)


@pytest.mark.parametrize("value", [None, 123, True, "", "123456", "https://example.test/menu",
    "A" * 43, QR_PREFIX + "A" * 42, QR_PREFIX + "A" * 44, QR_PREFIX + "A" * 42 + "B",
    QR_PREFIX + "A" * 43 + "=", QR_PREFIX + "A" * 42 + "ñ", "MITBOL:loyalty:v1:" + "A" * 43,
    QR_PREFIX + "A" * 43 + "\n", " " + QR_PREFIX + "A" * 43])
def test_invalid_or_noncanonical_values_are_rejected(value):
    with pytest.raises(ValueError):
        hash_identification(value)
    with pytest.raises(ValidationError):
        LoyaltyIdentificationInput(qr_payload=value)


def test_response_has_no_personal_identity_and_does_not_echo_secrets_in_errors():
    token = generate_identification()
    now = datetime.now(timezone.utc)
    read = LoyaltyIdentificationRead(qr_payload=token.qr_payload, issued_at=now, expires_at=now + timedelta(minutes=5))
    assert token.qr_payload not in repr(read)
    assert set(read.model_dump()) == {"qr_payload", "issued_at", "expires_at", "identification_type", "purpose"}
    assert read.identification_type == "qr" and read.purpose == "visit_identification"
    with pytest.raises(ValidationError) as error:
        LoyaltyIdentificationInput(qr_payload=token.qr_payload, account_id=1)
    assert token.qr_payload not in str(error.value)


@pytest.mark.parametrize("case", ["naive_issue", "naive_expiration", "equal", "past"])
def test_response_requires_timezone_and_positive_validity(case):
    now = datetime.now(timezone.utc)
    issued, expires = now, now + timedelta(minutes=5)
    if case == "naive_issue":
        issued = now.replace(tzinfo=None)
    elif case == "naive_expiration":
        expires = expires.replace(tzinfo=None)
    elif case == "equal":
        expires = now
    else:
        expires = now - timedelta(seconds=1)
    with pytest.raises(ValidationError):
        LoyaltyIdentificationRead(qr_payload=generate_identification().qr_payload, issued_at=issued, expires_at=expires)


def test_migration_is_offline_without_tokens_data_or_grants(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La migración offline intentó conectarse")
    monkeypatch.setattr(Engine, "connect", forbidden)
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "LoyaltyIdentifications"' in sql
    assert "INSERT INTO" not in sql and "GRANT " not in sql and "DROP TABLE" not in sql


def test_migration_matches_model_and_roundtrips_without_changing_accounts_or_sessions(backend):
    b = backend
    emitter_session(b)
    with b.sessions.kw["bind"].begin() as db:
        assert validate_bridge_schema(db) == REVISION
        metadata = [Base.metadata, AuthBase.metadata, SyncBase.metadata, OrderBase.metadata, PasswordRecoveryBase.metadata,
            CouponBase.metadata, CommercialDiscountBase.metadata, OrderStateBase.metadata, OrderPaymentBase.metadata,
            LoyaltyBase.metadata, LoyaltyIdentificationBase.metadata]
        assert not compare_metadata(MigrationContext.configure(db), metadata)
        assert db.execute(select(LoyaltyIdentification)).first() is None
        accounts = db.execute(select(PersonalAccount.__table__)).all()
        sessions = db.execute(select(AccountSession.__table__)).all()
        command.downgrade(config_for(db), PREVIOUS)
        assert "LoyaltyIdentifications" not in inspect(db).get_table_names()
        assert validate_bridge_schema(db) == PREVIOUS
        command.upgrade(config_for(db), REVISION)
        assert validate_bridge_schema(db) == REVISION
        assert db.execute(select(PersonalAccount.__table__)).all() == accounts
        assert db.execute(select(AccountSession.__table__)).all() == sessions


def test_storage_keeps_only_digest_and_correlated_consumption_fields(backend):
    b = backend
    session, token = emitter_session(b), generate_identification()
    with b.sessions.begin() as db:
        row = record(b, session, token_hash=token.digest)
        db.add(row)
        db.flush()
        stored = db.execute(select(LoyaltyIdentification.__table__)).one()
        assert token.qr_payload not in {str(value) for value in stored}
        assert token.qr_payload[len(QR_PREFIX):] not in {str(value) for value in stored}
        assert row.token_hash == token.digest and row.consumed_at is None and row.consumed_by_account_id is None
        row.consumed_at = b.now.value + timedelta(minutes=1)
        row.consumed_by_account_id = b.ids["staff"][0]
    with b.sessions() as db:
        row = db.get(LoyaltyIdentification, session.account_id)
        assert row.consumed_at.tzinfo is not None and row.consumed_by_account_id == b.ids["staff"][0]


@pytest.mark.parametrize("case,constraint", [
    ("hash", "ck_LoyaltyIdentifications_hash"), ("version", "ck_LoyaltyIdentifications_auth_version"),
    ("expiry", "ck_LoyaltyIdentifications_expiration"), ("actor_only", "ck_LoyaltyIdentifications_consumption"),
    ("time_only", "ck_LoyaltyIdentifications_consumption"), ("early_use", "ck_LoyaltyIdentifications_consumed_time"),
    ("expired_use", "ck_LoyaltyIdentifications_consumed_time"), ("session", "fk_LoyaltyIdentifications_session_id"),
    ("account", "fk_LoyaltyIdentifications_account_id"), ("actor", "fk_LoyaltyIdentifications_consumed_by_account_id"),
])
def test_invalid_stored_credential_is_rejected(backend, case, constraint):
    b = backend
    session = emitter_session(b)
    changes = {
        "hash": {"token_hash": "raw-secret"}, "version": {"auth_version": -1}, "expiry": {"expires_at": b.now.value},
        "actor_only": {"consumed_by_account_id": b.ids["staff"][0]}, "time_only": {"consumed_at": b.now.value},
        "early_use": {"consumed_at": b.now.value - timedelta(seconds=1), "consumed_by_account_id": b.ids["staff"][0]},
        "expired_use": {"consumed_at": b.now.value + timedelta(minutes=5), "consumed_by_account_id": b.ids["staff"][0]},
        "session": {"session_id": uuid4()}, "account": {"account_id": -1},
        "actor": {"consumed_at": b.now.value, "consumed_by_account_id": -1},
    }[case]
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.add(record(b, session, **changes))
        db.flush()
    assert error.value.orig.diag.constraint_name == constraint


def test_same_digest_cannot_identify_two_accounts_and_session_cleanup_removes_the_slot(backend):
    b = backend
    customer, admin = emitter_session(b), emitter_session(b, "admin")
    token = generate_identification()
    with b.sessions.begin() as db:
        db.add(record(b, customer, token_hash=token.digest))
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.add(record(b, admin, token_hash=token.digest))
        db.flush()
    assert error.value.orig.diag.constraint_name == "uq_LoyaltyIdentifications_token_hash"
    with b.sessions.begin() as db:
        db.execute(delete(AccountSession).where(AccountSession.session_id == customer.session_id))
        assert db.get(LoyaltyIdentification, customer.account_id) is None


def test_concurrent_creations_cannot_leave_two_identifications_for_one_account(backend):
    b = backend
    session, barrier = emitter_session(b), Barrier(2)
    def save():
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                db.add(record(b, session))
                db.flush()
            return "saved"
        except IntegrityError as error:
            assert error.orig.diag.constraint_name == "pk_LoyaltyIdentifications"
            return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == ["duplicate", "saved"]

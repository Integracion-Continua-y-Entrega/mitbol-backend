"""HU-043: integridad del registro y día local, sin endpoint ni visitas reales."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from threading import Barrier
from unittest.mock import Mock

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.auth_errors import AuthError
from app.core.loyalty_identification import generate_identification, hash_identification
from app.db.schema_compatibility import validate_bridge_schema
from app.models import (
    AccountSession, AuthBase, Base, CommercialDiscountBase, CouponBase, LoyaltyBase, LoyaltyIdentification,
    LoyaltyIdentificationBase, LoyaltyVisit, LoyaltyVisitBase, OrderBase, OrderPaymentBase, OrderStateBase,
    PasswordRecoveryBase, PersonalAccount,
)
from app.models.sync_run import SyncBase
from app.schemas.loyalty_visits import LoyaltyVisitRead
from app.services.visit_calendar_service import DEFAULT_RESTAURANT_TIMEZONE, visit_day
from test_auth_api import auth_database, backend as auth_backend, csrf_headers, login, password_hash
from test_auth_persistence import config_for


REVISION = "20261125_0018"
PREVIOUS = "20261124_0017"
POINT = datetime(2026, 11, 25, 18, tzinfo=timezone.utc)


@pytest.fixture
def backend(auth_backend):
    b = auth_backend
    with b.sessions.kw["bind"].begin() as db:
        command.upgrade(config_for(db), REVISION)
    try:
        yield b
    finally:
        with b.sessions.begin() as db:
            db.execute(delete(LoyaltyVisit))
            db.execute(delete(LoyaltyIdentification))


def record(b, **changes):
    """Evidencia exclusivamente sintética; la futura ruta obtendrá identidad y fecha desde el servidor."""
    return LoyaltyVisit(**({"account_id": b.ids["customer"][0], "visited_on": date(2026, 11, 25),
        "registered_at": POINT, "restaurant_timezone": DEFAULT_RESTAURANT_TIMEZONE,
        "registered_by_account_id": b.ids["staff"][0], "identification_hash": generate_identification().digest} | changes))


@pytest.mark.parametrize("point,zone,expected", [
    (datetime(2026, 11, 25, 5, 59, 59, tzinfo=timezone.utc), "America/Mexico_City", date(2026, 11, 24)),
    (datetime(2026, 11, 25, 6, 0, tzinfo=timezone.utc), "America/Mexico_City", date(2026, 11, 25)),
    (datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc), "UTC", date(2026, 11, 25)),
    (datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc), "Asia/Tokyo", date(2026, 11, 26)),
])
def test_local_day_uses_explicit_zone_independently_of_database_session_timezone(backend, point, zone, expected):
    with backend.sessions.begin() as db:
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        assert visit_day(db, point, restaurant_timezone=zone) == expected
        db.execute(text("SET LOCAL TIME ZONE 'Pacific/Auckland'"))
        assert visit_day(db, point, restaurant_timezone=zone) == expected


@pytest.mark.parametrize("point,zone", [(None, "UTC"), (datetime(2026, 11, 25), "UTC"),
    (POINT, ""), (POINT, " UTC "), (POINT, "../../zone"), (POINT, None), (POINT, "x" * 65)])
def test_invalid_calendar_arguments_fail_before_querying_the_database(point, zone):
    db = Mock()
    with pytest.raises(AuthError) as error:
        visit_day(db, point, restaurant_timezone=zone)
    assert error.value.status_code == 503 and db.mock_calls == []


def test_unknown_timezone_is_a_configuration_error(backend):
    with backend.sessions() as db, pytest.raises(AuthError) as error:
        visit_day(db, POINT, restaurant_timezone="Invalid/Restaurant")
    assert error.value.status_code == 503


def test_incremental_migration_is_offline_without_automatic_visits_or_grants(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("La migración offline intentó conectarse")
    monkeypatch.setattr(Engine, "connect", forbidden)
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "LoyaltyVisits"' in sql
    assert "INSERT INTO" not in sql and "GRANT " not in sql and "DROP TABLE" not in sql


def test_migration_matches_model_and_preserves_identity_data(backend):
    b = backend
    with b.sessions.kw["bind"].begin() as db:
        assert validate_bridge_schema(db) == REVISION
        metadata = [Base.metadata, AuthBase.metadata, SyncBase.metadata, OrderBase.metadata, PasswordRecoveryBase.metadata,
            CouponBase.metadata, CommercialDiscountBase.metadata, OrderStateBase.metadata, OrderPaymentBase.metadata,
            LoyaltyBase.metadata, LoyaltyIdentificationBase.metadata, LoyaltyVisitBase.metadata]
        assert not compare_metadata(MigrationContext.configure(db), metadata)
        assert db.execute(select(LoyaltyVisit)).first() is None
        accounts = db.execute(select(PersonalAccount.__table__)).all()
        command.downgrade(config_for(db), PREVIOUS)
        assert "LoyaltyVisits" not in inspect(db).get_table_names()
        assert validate_bridge_schema(db) == PREVIOUS
        command.upgrade(config_for(db), REVISION)
        assert validate_bridge_schema(db) == REVISION
        assert db.execute(select(PersonalAccount.__table__)).all() == accounts


def test_receipt_preserves_local_date_and_actor_without_publishing_identification_hash(backend):
    b = backend
    with b.sessions.begin() as db:
        row = record(b)
        db.add(row)
        db.flush()
        receipt = LoyaltyVisitRead.model_validate(row)
        assert receipt.account_id == b.ids["customer"][0]
        assert receipt.registered_by_account_id == b.ids["staff"][0]
        assert receipt.visited_on == date(2026, 11, 25)
        assert receipt.registered_at == POINT and receipt.restaurant_timezone == DEFAULT_RESTAURANT_TIMEZONE
        assert "identification_hash" not in receipt.model_dump()
        assert row.identification_hash not in receipt.model_dump_json()


@pytest.mark.parametrize("change,constraint", [
    ({"identification_hash": "raw-code"}, "ck_LoyaltyVisits_identification_hash"),
    ({"visited_on": date(2026, 11, 26)}, "ck_LoyaltyVisits_local_date"),
    ({"account_id": -1}, "fk_LoyaltyVisits_account_id"),
    ({"registered_by_account_id": -1}, "fk_LoyaltyVisits_registered_by_account_id"),
])
def test_database_rejects_incoherent_evidence(backend, change, constraint):
    with pytest.raises(IntegrityError) as error, backend.sessions.begin() as db:
        db.add(record(backend, **change))
        db.flush()
    assert error.value.orig.diag.constraint_name == constraint


def test_infinite_registration_time_is_rejected(backend):
    with pytest.raises(IntegrityError) as error, backend.sessions.begin() as db:
        db.add(record(backend, registered_at=text("'infinity'::timestamptz"), visited_on=text("'infinity'::date")))
        db.flush()
    assert error.value.orig.diag.constraint_name == "ck_LoyaltyVisits_registered_at"


def test_different_qrs_for_same_account_and_day_do_not_duplicate_a_visit(backend):
    b = backend
    with b.sessions.begin() as db:
        db.add(record(b))
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.add(record(b))
        db.flush()
    assert error.value.orig.diag.constraint_name == "uq_LoyaltyVisits_account_day"


def test_a_used_qr_cannot_be_associated_with_a_second_day(backend):
    b = backend
    token = generate_identification()
    with b.sessions.begin() as db:
        db.add(record(b, identification_hash=token.digest))
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.add(record(b, registered_at=POINT + timedelta(days=1), visited_on=date(2026, 11, 26), identification_hash=token.digest))
        db.flush()
    assert error.value.orig.diag.constraint_name == "uq_LoyaltyVisits_identification_hash"


def test_distinct_calendar_days_can_be_seconds_apart_without_a_rolling_24_hour_rule(backend):
    b = backend
    before = datetime(2026, 11, 25, 5, 59, 59, tzinfo=timezone.utc)
    after = before + timedelta(seconds=1)
    with b.sessions.begin() as db:
        for instant in (before, after):
            db.add(record(b, registered_at=instant, visited_on=visit_day(db, instant)))
        db.flush()
        assert len(db.scalars(select(LoyaltyVisit)).all()) == 2


@pytest.mark.parametrize("role,constraint", [("customer", "fk_LoyaltyVisits_account_id"), ("staff", "fk_LoyaltyVisits_registered_by_account_id")])
def test_visit_evidence_prevents_deleting_the_customer_or_responsible_account(backend, role, constraint):
    b = backend
    with b.sessions.begin() as db:
        db.add(record(b))
    with pytest.raises(IntegrityError) as error, b.sessions.begin() as db:
        db.execute(delete(PersonalAccount).where(PersonalAccount.account_id == b.ids[role][0]))
    assert error.value.orig.diag.constraint_name == constraint


def test_evidence_survives_qr_renewal_and_session_cleanup(backend):
    b = backend
    headers = csrf_headers(login(b, "customer"))
    issued = b.client.post("/api/auth/loyalty/identification", headers=headers, json={})
    assert issued.status_code == 201
    digest = hash_identification(issued.json()["qr_payload"])
    with b.sessions.begin() as db:
        db.add(record(b, identification_hash=digest))
    renewed = b.client.post("/api/auth/loyalty/identification", headers=headers, json={})
    assert renewed.status_code == 201 and hash_identification(renewed.json()["qr_payload"]) != digest
    with b.sessions.begin() as db:
        credential = db.get(LoyaltyIdentification, b.ids["customer"][0])
        db.execute(delete(AccountSession).where(AccountSession.session_id == credential.session_id))
    with b.sessions() as db:
        assert db.get(LoyaltyIdentification, b.ids["customer"][0]) is None
        assert db.scalar(select(LoyaltyVisit)).identification_hash == digest


def test_concurrent_insertions_for_one_day_have_only_one_winner(backend):
    b, barrier = backend, Barrier(2)
    def save():
        try:
            with b.sessions.begin() as db:
                barrier.wait(timeout=5)
                db.add(record(b))
                db.flush()
            return "saved"
        except IntegrityError as error:
            assert error.orig.diag.constraint_name == "uq_LoyaltyVisits_account_day"
            return "duplicate"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == ["duplicate", "saved"]

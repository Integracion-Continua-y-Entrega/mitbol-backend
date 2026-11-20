"""Delta HU-013 y roundtrip exclusivamente en PostgreSQL desechable."""

from datetime import datetime, timedelta, timezone
from io import StringIO

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.models import AuthBase, Base, OrderBase, PasswordRecoveryBase, PasswordResetToken, Person, PersonalAccount
from app.models.sync_run import SyncBase
from test_auth_persistence import config_for


PREVIOUS = "20261119_0010"
REVISION = "20261119_0011"


def test_recovery_delta_creates_only_hashed_tokens_without_external_changes():
    output = StringIO()
    command.upgrade(config_for(output=output), f"{PREVIOUS}:{REVISION}", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE "PasswordResetTokens"' in sql
    assert "ON DELETE CASCADE" in sql and "UNIQUE (token_hash)" in sql
    assert not any(operation in sql for operation in ("ALTER TABLE", "CREATE ROLE", "GRANT ", "DROP TABLE", "INSERT INTO"))


def test_upgrade_matches_models_enforces_constraints_and_roundtrips_without_changing_accounts(empty_postgresql_connection):
    db = empty_postgresql_connection
    config = config_for(db)
    command.upgrade(config, PREVIOUS)
    before = set(inspect(db).get_table_names())
    person_id = db.scalar(Person.__table__.insert().values(name="Conservar identidad", phone="2281234567").returning(Person.person_id))
    # Hash sintético compatible con el CHECK; no se usa para autenticación.
    account_id = db.scalar(PersonalAccount.__table__.insert().values(person_id=person_id, email="recovery@example.test",
        password_hash="$argon2id$historical", is_active=True).returning(PersonalAccount.account_id))
    command.upgrade(config, REVISION)
    assert set(inspect(db).get_table_names()) == before | {"PasswordResetTokens"}
    assert compare_metadata(MigrationContext.configure(db), [Base.metadata, AuthBase.metadata, SyncBase.metadata,
                            OrderBase.metadata, PasswordRecoveryBase.metadata]) == []
    now = datetime.now(timezone.utc)
    values = dict(account_id=account_id, token_hash="a" * 64, auth_version=0, created_at=now, expires_at=now + timedelta(minutes=30))
    for changes in ({"token_hash": "raw-secret"}, {"auth_version": -1}, {"expires_at": now}, {"account_id": account_id + 999}):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(PasswordResetToken.__table__.insert().values(values | changes))
    db.execute(PasswordResetToken.__table__.insert().values(values))
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(PasswordResetToken.__table__.insert().values(values | {"token_hash": "b" * 64}))
    assert db.scalar(select(PersonalAccount.password_hash).where(PersonalAccount.account_id == account_id)) == "$argon2id$historical"
    command.downgrade(config, PREVIOUS)
    assert set(inspect(db).get_table_names()) == before
    assert db.scalar(select(PersonalAccount.email).where(PersonalAccount.account_id == account_id)) == "recovery@example.test"
    command.upgrade(config, REVISION)
    assert db.scalar(select(PasswordResetToken.account_id)) is None
    db.execute(PasswordResetToken.__table__.insert().values(values))
    db.execute(PersonalAccount.__table__.delete().where(PersonalAccount.account_id == account_id))
    assert db.scalar(select(PasswordResetToken.account_id)) is None

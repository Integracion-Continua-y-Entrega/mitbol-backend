"""HU-009: login real, rol web limitado y lector público sin acceso comercial."""

from datetime import datetime, timezone
from decimal import Decimal

from alembic import command
import pytest
from sqlalchemy import text

from test_auth_persistence import auth_database, config_for
from test_auth_api import password_hash, login
from test_auth_api import backend as auth_backend
from test_auth_admin_api import backend as catalog_backend
from test_admin_writer import backend as writer_backend, writer_database, denied, execute_script, ROOT, VERIFY


@pytest.fixture
def backend(writer_backend):
    b = writer_backend
    with b.roles.owner.begin() as db:
        command.upgrade(config_for(db), "20261111_0006")
    grant = (ROOT / "docs/database/update_admin_writer_discounts.sql").read_text(encoding="utf-8")
    execute_script(b.roles.owner, "SET mitbol.approved_admin_write = 'hu009-reviewed';\n" + grant)
    with b.roles.owner.begin() as db:
        db.execute(text('''INSERT INTO public."Discounts"
            (loyverse_id, name, discount_type, value, scope, is_active, synced_at)
            VALUES ('synthetic-discount', 'Consulta administrativa', 'FIXED_AMOUNT', :value, 'unknown', false, :stamp)
        '''), {"value": Decimal("35.1234567890123456789"), "stamp": datetime.now(timezone.utc)})
    try:
        yield b
    finally:
        with b.roles.owner.begin() as db:
            command.downgrade(config_for(db), "20261106_0005")


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_real_login_reads_discounts_with_web_role_and_no_public_access(backend, role):
    b = backend
    assert b.client.get("/api/admin/discounts").status_code == 401
    assert login(b, role).status_code == 200
    response = b.client.get("/api/admin/discounts?limit=1&offset=0")
    assert response.status_code == 200
    assert response.json()[0]["value"] == "35.1234567890123456789"
    assert response.json()[0]["is_active"] is False
    assert b.client.get("/api/admin/discounts?offset=1").json() == []
    assert b.client.get("/api/admin/discounts?limit=101").status_code == 422
    execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
    denied(b.roles.reader, 'SELECT * FROM public."Discounts"')


@pytest.mark.parametrize("statement", [
    'UPDATE public."Discounts" SET name = \'prohibido\'',
    'DELETE FROM public."Discounts"',
    'INSERT INTO public."Discounts" (loyverse_id,name,discount_type,scope) VALUES (\'x\',\'x\',\'VARIABLE_AMOUNT\',\'unknown\')',
    'SELECT nextval(\'public."Discounts_discount_id_seq"\')',
])
def test_web_role_cannot_synchronize_or_consume_discount_sequence(backend, statement):
    denied(backend.roles.writer, statement)


def test_verifier_rejects_leaked_discount_write_grant(backend):
    from sqlalchemy.exc import DBAPIError
    b = backend
    with b.roles.owner.begin() as db:
        db.exec_driver_sql('GRANT UPDATE(value) ON public."Discounts" TO mitbol_admin_writer')
    try:
        with pytest.raises(DBAPIError, match="WRITER_COLUMN"):
            execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
    finally:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql('REVOKE UPDATE(value) ON public."Discounts" FROM mitbol_admin_writer')


def test_cli_preflight_is_read_only_and_does_not_consume_sequence(backend):
    from app.cli.sync_discounts import preflight
    b = backend
    with b.roles.owner.connect() as db:
        before = db.exec_driver_sql('SELECT last_value, is_called FROM public."Discounts_discount_id_seq"').one()
    report = preflight(b.roles.owner, expected_database=b.roles.owner.url.database)
    assert report["writes"] is False and report["loyverse_contacted"] is False
    assert report["discount_count"] == 1
    with b.roles.owner.connect() as db:
        assert db.exec_driver_sql('SELECT last_value, is_called FROM public."Discounts_discount_id_seq"').one() == before


def test_previous_schema_returns_service_unavailable_without_breaking_login(writer_backend):
    b = writer_backend
    assert login(b).status_code == 200
    response = b.client.get("/api/admin/discounts")
    assert response.status_code == 503
    assert "actualizar el esquema" in response.json()["detail"]


def test_new_writer_profile_on_discount_revision_is_read_only_for_discounts(backend):
    from sqlalchemy import create_engine
    role = "mitbol_hu009_writer_test"
    owner = backend.roles.owner
    prepared = (ROOT / "docs/database/prepare_admin_writer_local.sql").read_text(encoding="utf-8")
    execute_script(owner, prepared.replace("mitbol_admin_writer", role))
    writer = None
    try:
        password = None
        if owner.url.password:
            password = "hu009-fixture"
            with owner.begin() as db:
                db.exec_driver_sql("ALTER ROLE mitbol_hu009_writer_test PASSWORD 'hu009-fixture'")
        writer = create_engine(owner.url.set(username=role, password=password), hide_parameters=True)
        execute_script(writer, VERIFY.read_text(encoding="utf-8").replace("mitbol_admin_writer", role))
        with writer.connect() as db:
            assert db.exec_driver_sql('SELECT name FROM public."Discounts"').scalar_one() == "Consulta administrativa"
        denied(writer, 'UPDATE public."Discounts" SET is_active = true')
        denied(writer, 'SELECT nextval(\'public."Discounts_discount_id_seq"\')')
    finally:
        if writer is not None:
            writer.dispose()
        with owner.begin() as db:
            db.exec_driver_sql("DROP OWNED BY mitbol_hu009_writer_test")
            db.exec_driver_sql("DROP ROLE mitbol_hu009_writer_test")

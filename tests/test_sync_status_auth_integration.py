"""HU-010: lectura con login real y ACL efectivas del rol web."""

import pytest
from sqlalchemy.exc import DBAPIError

from test_auth_persistence import auth_database
from test_auth_api import password_hash, login, backend as auth_backend
from test_auth_admin_api import backend as catalog_backend
from test_admin_writer import writer_database, backend as writer_backend, denied, execute_script, ROOT, VERIFY
from test_discount_auth_integration import backend as discount_backend


@pytest.fixture
def backend(discount_backend):
    return discount_backend


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_login_reads_five_entities_with_minimal_web_permissions(backend, role):
    b = backend
    assert b.client.get("/api/admin/sync/status").status_code == 401
    assert login(b, role).status_code == 200
    response = b.client.get("/api/admin/sync/status")
    assert response.status_code == 200
    body = response.json()
    assert body["history_available"] is False and len(body["entities"]) == 5
    rows = {row["entity"]: row for row in body["entities"]}
    # La fixture de catálogo incluye el producto principal y otro inactivo.
    assert rows["Products"]["total_count"] == 2
    assert rows["Products"]["synced_count"] == 2
    assert rows["Discounts"]["synced_count"] == 1
    assert rows["ProductModifiers"]["total_count"] == 0
    execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))


@pytest.mark.parametrize("statement", [
    'SELECT name FROM public."ProductModifiers"',
    'SELECT price FROM public."ProductComplements"',
    'UPDATE public."ProductModifiers" SET synced_at = now()',
    'DELETE FROM public."ProductComplements"',
    'SELECT nextval(\'public."ProductModifiers_product_modifier_id_seq"\')',
])
def test_only_required_dates_are_readable_and_no_commercial_writes(backend, statement):
    denied(backend.roles.writer, statement)


def test_missing_date_grant_returns_503_and_increment_restores_read(backend):
    b = backend
    assert login(b).status_code == 200
    with b.roles.owner.begin() as db:
        db.exec_driver_sql('REVOKE SELECT(synced_at) ON public."ProductModifiers", public."ProductComplements" FROM mitbol_admin_writer')
    try:
        assert b.client.get("/api/admin/sync/status").status_code == 503
        with pytest.raises(DBAPIError, match="WRITER_COLUMN"):
            execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
        source = (ROOT / "docs/database/update_admin_writer_sync_status.sql").read_text(encoding="utf-8")
        execute_script(b.roles.owner, "SET mitbol.approved_admin_write = 'hu010-status-reviewed';\n" + source)
        assert b.client.get("/api/admin/sync/status").status_code == 200
        execute_script(b.roles.writer, VERIFY.read_text(encoding="utf-8"))
    finally:
        with b.roles.owner.begin() as db:
            db.exec_driver_sql('GRANT SELECT(synced_at) ON public."ProductModifiers", public."ProductComplements" TO mitbol_admin_writer')


def test_old_schema_returns_503_without_partial_entities(writer_backend):
    b = writer_backend
    assert login(b).status_code == 200
    response = b.client.get("/api/admin/sync/status")
    assert response.status_code == 503
    assert "entities" not in response.json()

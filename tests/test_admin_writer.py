"""ACL y HTTP con LOGIN real limitado, únicamente en PostgreSQL 18 temporal."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from alembic import command
from alembic.util import CommandError
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, delete, event, text
from sqlalchemy.exc import DBAPIError

from app.services.sync_service import CATALOG_SYNC_LOCK_KEY
from copy import deepcopy
from test_auth_persistence import auth_database, config_for
from test_auth_api import backend as auth_backend, password_hash, login, csrf_headers
from test_auth_admin_api import audit_rows, backend as catalog_backend, catalog_state, product_path
import test_auth_admin_api as admin_tests
import test_auth_api as auth_tests
from app.db.connection import READ_ONLY_ROLE_SQL
from app.main import create_app
from app.models import EmployeeRole, Product
from app.repositories import auth as repo

ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "docs/database/prepare_admin_writer_local.sql"
VERIFY = ROOT / "docs/database/verify_admin_writer_local.sql"
HIGHLIGHTS_DELTA = ROOT / "docs/database/update_admin_writer_highlights.sql"


def execute_script(engine, sql):
    # Se conservan BEGIN/COMMIT del archivo; error siempre revierte su unidad.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
        try:
            return db.exec_driver_sql(sql, execution_options={"no_parameters": True})
        except Exception:
            db.exec_driver_sql("ROLLBACK")
            raise


@pytest.fixture(scope="module")
def writer_database(auth_database):
    owner = auth_database
    # auth_database ya contrastó directorio temporal, puerto, operador y PG18.
    reader_sql = (ROOT / "docs/database/prepare_render_reader.sql").read_text(encoding="utf-8")
    reader_sql = reader_sql.replace("mitbol_cloud_dev", owner.url.database).replace("neondb_owner", "mitbol_ci")
    # Sólo la copia efímera: lector idéntico después de migrar auth en ESTA base.
    reader_sql = reader_sql.replace("20261102_0003", "20261106_0005")
    execute_script(owner, reader_sql)
    writer_created = False
    try:
        execute_script(owner, PREPARE.read_text(encoding="utf-8"))
        writer_created = True
        # Contraseñas exclusivamente sintéticas para el servicio CI con SCRAM.
        if owner.url.password:
            with owner.begin() as db:
                db.exec_driver_sql("ALTER ROLE mitbol_admin_writer PASSWORD 'mitbol-writer-fixture'")
                db.exec_driver_sql("ALTER ROLE mitbol_render_reader PASSWORD 'mitbol-reader-fixture'")
        writer_url = owner.url.set(username="mitbol_admin_writer",
                                  password="mitbol-writer-fixture" if owner.url.password else None)
        reader_url = owner.url.set(username="mitbol_render_reader",
                                  password="mitbol-reader-fixture" if owner.url.password else None)
        writer = create_engine(writer_url, hide_parameters=True)
        reader = create_engine(reader_url, hide_parameters=True)
        try:
            yield SimpleNamespace(owner=owner, writer=writer, reader=reader, writer_url=writer_url,
                                  reader_url=reader_url)
        finally:
            writer.dispose()
            reader.dispose()
    finally:
        with owner.begin() as db:
            for role in (("mitbol_admin_writer", "mitbol_render_reader") if writer_created else ("mitbol_render_reader",)):
                if db.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"), {"role": role}):
                    db.exec_driver_sql(f"DROP OWNED BY {role}")
                    db.exec_driver_sql(f"DROP ROLE {role}")


@pytest.fixture
def backend(catalog_backend, writer_database):
    original = catalog_backend
    config = original.config.model_copy(update={
        "database_url": writer_database.reader_url.render_as_string(hide_password=False),
        "auth_database_url": writer_database.writer_url.render_as_string(hide_password=False),
        "image_storage_root": original.storage.root,
    })
    app = create_app(config)
    app.state.auth_service.clock = lambda: original.now.value
    observed = []

    def watch(label):
        def observe(connection, cursor, statement, parameters, context, many):
            # No registra parámetros, contraseñas ni valores de sesión.
            observed.append((label, statement, id(connection), id(connection.get_transaction())))
        return observe

    for label, factory in (("reader", app.state.session_factory), ("writer", app.state.auth_session_factory)):
        event.listen(factory.kw["bind"], "before_cursor_execute", watch(label))
    with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54341)) as client:
        b = SimpleNamespace(**vars(original))
        b.app, b.client, b.config = app, client, config
        b.service, b.storage = app.state.auth_service, app.state.image_storage
        b.observed, b.roles = observed, writer_database
        yield b


def denied(engine, statement, parameters=None, code="42501"):
    with engine.connect() as db:
        if statement.startswith("CREATE DATABASE"):
            db = db.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(DBAPIError) as error:
            db.execute(text(statement), parameters or {})
        assert error.value.orig.pgcode == code
        db.rollback()


def test_fresh_writer_login_has_exact_policy_and_reader_still_passes(writer_database):
    r = writer_database
    execute_script(r.writer, VERIFY.read_text(encoding="utf-8"))
    with r.writer.connect() as db:
        assert db.execute(text("SELECT current_user, session_user")).one() == ("mitbol_admin_writer",) * 2
        assert db.scalar(text(READ_ONLY_ROLE_SQL)) is True
        assert not db.scalar(text("SELECT has_table_privilege(current_user, '\"Products\"', 'UPDATE')"))
    with r.reader.connect() as db:
        assert db.execute(text("SELECT current_user, session_user")).one() == ("mitbol_render_reader",) * 2
        assert db.scalar(text("SHOW default_transaction_read_only")) == "on"
        assert db.scalar(text(READ_ONLY_ROLE_SQL)) is False
    sql = (ROOT / "docs/database/verify_render_reader.sql").read_text(encoding="utf-8")
    sql = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
    execute_script(r.reader, sql.replace("mitbol_cloud_dev", r.owner.url.database))


def test_prepare_rejects_reexecution_and_wrong_database_without_changing_role(writer_database):
    r = writer_database
    with pytest.raises(DBAPIError, match="WRITER_EXISTS"):
        execute_script(r.owner, PREPARE.read_text(encoding="utf-8"))
    control = create_engine(r.owner.url.set(database="postgres"), hide_parameters=True)
    try:
        with pytest.raises(DBAPIError, match="WRITER_LOCAL_TARGET"):
            execute_script(control, PREPARE.read_text(encoding="utf-8"))
    finally:
        control.dispose()
    execute_script(r.writer, VERIFY.read_text(encoding="utf-8"))


def highlights_delta_sql(direction):
    source = HIGHLIGHTS_DELTA.read_text(encoding="utf-8")
    assert direction in {"upgrade", "rollback"}
    return source.replace("BEGIN;", "BEGIN;\nSET LOCAL mitbol.approved_admin_write = 'hu006-reviewed';\n"
                          f"SET LOCAL mitbol.hu006_direction = '{direction}';", 1)


def test_highlights_delta_requires_explicit_approval(writer_database):
    with pytest.raises(DBAPIError, match="HU006_NOT_APPROVED"):
        execute_script(writer_database.owner, HIGHLIGHTS_DELTA.read_text(encoding="utf-8"))
    execute_script(writer_database.writer, VERIFY.read_text(encoding="utf-8"))


@pytest.mark.parametrize("role", ["writer", "reader"])
def test_highlights_delta_rejects_non_operator_login(writer_database, role):
    with pytest.raises(DBAPIError, match="HU006_WRONG_TARGET_OR_OPERATOR"):
        execute_script(getattr(writer_database, role), highlights_delta_sql("upgrade"))
    execute_script(writer_database.writer, VERIFY.read_text(encoding="utf-8"))


def test_highlights_delta_rejects_maintenance_database(writer_database):
    control = create_engine(writer_database.owner.url.set(database="postgres"), hide_parameters=True)
    try:
        with pytest.raises(DBAPIError, match="HU006_WRONG_TARGET_OR_OPERATOR"):
            execute_script(control, highlights_delta_sql("upgrade"))
    finally:
        control.dispose()


def test_highlights_delta_roundtrip_changes_only_two_column_grants(writer_database):
    r = writer_database

    def column_policy():
        with r.owner.connect() as db:
            return dict(db.execute(text("""SELECT attname,
                has_column_privilege('mitbol_admin_writer', 'public."Products"', attname, 'UPDATE')
                FROM pg_attribute WHERE attrelid = 'public."Products"'::regclass
                  AND attnum > 0 AND NOT attisdropped""")).all())

    upgraded = column_policy()
    try:
        execute_script(r.owner, highlights_delta_sql("rollback"))
        previous = column_policy()
        assert {key for key in upgraded if previous[key] != upgraded[key]} == {"is_featured", "is_new"}
        assert previous["is_featured"] is previous["is_new"] is False
        denied(r.writer, 'UPDATE "Products" SET is_featured = true')
        denied(r.writer, 'UPDATE "Products" SET is_new = false')
        execute_script(r.owner, highlights_delta_sql("upgrade"))
        assert column_policy() == upgraded
        execute_script(r.owner, highlights_delta_sql("upgrade"))  # Idempotente.
        execute_script(r.writer, VERIFY.read_text(encoding="utf-8"))
        with r.reader.connect() as db:
            assert not db.scalar(text("SELECT has_column_privilege(current_user, 'public.\"Products\"', 'is_featured', 'UPDATE')"))
            assert not db.scalar(text("SELECT has_column_privilege(current_user, 'public.\"Products\"', 'is_new', 'UPDATE')"))
    finally:
        # Solo el laboratorio guardado por writer_database; restaurar ante fallo.
        with r.owner.begin() as db:
            db.exec_driver_sql('GRANT UPDATE (is_featured, is_new) ON public."Products" TO mitbol_admin_writer')


@pytest.mark.parametrize("role", ["writer", "reader"])
def test_prepare_rejects_non_operator_logins_without_changing_acl(writer_database, role):
    r = writer_database
    with pytest.raises(DBAPIError, match="WRITER_LOCAL_TARGET"):
        execute_script(getattr(r, role), PREPARE.read_text(encoding="utf-8"))
    execute_script(r.writer, VERIFY.read_text(encoding="utf-8"))


@pytest.mark.parametrize("table", ["Persons", "PersonalAccounts", "Employees", "Roles", "EmployeeRoles", "AccountSessions", "AuditEvents"])
def test_reader_cannot_read_authentication_even_after_writer_setup(writer_database, table):
    denied(writer_database.reader, f'SELECT * FROM "{table}"')


def test_description_and_transaction_trace_use_only_writer(backend):
    b = backend
    admin_tests.test_admin_description_and_public_contract_preserve_commercial_fields(b)
    mutation = [row for row in b.observed if row[1].startswith('UPDATE "Products"')]
    audits = [row for row in b.observed if row[1].startswith('INSERT INTO "AuditEvents"') and row[2:] == mutation[0][2:]]
    assert len(mutation) == len(audits) == 1 and mutation[0][0] == audits[0][0] == "writer"


def test_staff_description_is_denied_with_real_writer_connection(backend):
    b = backend
    response = b.client.patch(admin_tests.product_path(b), headers=csrf_headers(login(b, "staff")),
                              json={"description": "Forbidden"})
    assert response.status_code == 403 and not admin_tests.audit_rows(b)
    assert not any(row[1].startswith('UPDATE "Products"') for row in b.observed)


def test_highlights_and_audit_share_real_writer_transaction(backend):
    b = backend
    admin_tests.test_admin_updates_highlights_and_public_menu_reflects_it(b)
    changes = [row for row in b.observed if row[1].startswith('UPDATE "Products"')]
    assert len(changes) == 1 and changes[0][0] == "writer"
    audits = [row for row in b.observed if row[1].startswith('INSERT INTO "AuditEvents"')
              and row[2:] == changes[0][2:]]
    assert len(audits) == 1 and audits[0][0] == "writer"


def test_staff_highlights_denied_with_real_writer_connection(backend):
    admin_tests.test_staff_cannot_update_highlights_and_preserves_catalog(backend)
    assert not any(row[1].startswith('UPDATE "Products"') for row in backend.observed)


@pytest.mark.parametrize("featured,new", [(False, False), (False, True), (True, False), (True, True)])
def test_writer_persists_each_highlight_configuration_without_other_changes(backend, featured, new):
    b = backend
    before = catalog_state(b)
    login_response = login(b)
    assert login_response.status_code == 200
    response = b.client.put(product_path(b, "highlights"), headers=csrf_headers(login_response),
                            json={"is_featured": featured, "is_new": new})
    assert response.status_code == 200
    assert response.json() == {"product_id": b.product_id, "is_featured": featured, "is_new": new}
    expected = deepcopy(before)
    product = next(row for row in expected["Products"] if row["product_id"] == b.product_id)
    product.update(is_featured=featured, is_new=new)
    assert catalog_state(b) == expected
    row, = audit_rows(b)
    assert row.action == "catalog.product.highlights"


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_highlights_missing_product_never_changes_catalog(backend, method):
    b = backend
    headers = csrf_headers(login(b))
    before = catalog_state(b)
    path = "/api/admin/products/2147483647/highlights"
    response = b.client.request(method, path, headers=headers,
                                json={"is_featured": True, "is_new": False} if method == "PUT" else None)
    assert response.status_code == 404
    assert catalog_state(b) == before
    assert not audit_rows(b)


@pytest.mark.parametrize("payload", [
    {}, {"is_featured": True}, {"is_featured": None, "is_new": False},
    {"is_featured": True, "is_new": 0}, {"is_featured": "true", "is_new": False},
    {"is_featured": True, "is_new": False, "is_active": True},
])
def test_writer_rejects_invalid_highlight_payload_before_modification(backend, payload):
    b = backend
    headers = csrf_headers(login(b))
    before = catalog_state(b)
    assert b.client.put(product_path(b, "highlights"), headers=headers, json=payload).status_code == 422
    assert catalog_state(b) == before
    assert not audit_rows(b)


def test_highlights_conflict_with_catalog_sync(backend):
    """El escritor rechaza cambios durante sincronización y continúa tras liberar el bloqueo."""
    b = backend
    login_response = login(b)
    assert login_response.status_code == 200
    before = catalog_state(b)
    path = product_path(b, "highlights")
    with b.roles.owner.begin() as connection:
        connection.execute(
            text('SELECT pg_advisory_xact_lock(:key)'),
            {'key': CATALOG_SYNC_LOCK_KEY},
        )
        response = b.client.put(path,
                                headers=csrf_headers(login_response),
                                json={"is_featured": True, "is_new": False})
        assert response.status_code == 409
        assert catalog_state(b) == before
        assert audit_rows(b) == []

    response = b.client.put(path,
                            headers=csrf_headers(login_response),
                            json={"is_featured": True, "is_new": False})
    assert response.status_code == 200
    assert response.json() == {"product_id": b.product_id, "is_featured": True, "is_new": False}
    row, = audit_rows(b)
    assert (row.action, row.actor_account_id, row.resource_type, row.resource_id, row.outcome) == (
            "catalog.product.highlights",
            b.ids["admin"][0],
            "product",
            str(b.product_id),
            "success"
        )


@pytest.mark.parametrize("role", ["admin", "staff"])
@pytest.mark.parametrize("resource", ["products", "variants"])
def test_real_availability_with_minimal_writer(backend, role, resource):
    admin_tests.test_both_roles_change_only_local_availability_and_public_menu_reflects_it(backend, role, resource)


def test_tags_with_minimal_writer(backend):
    admin_tests.test_tags_normalization_validation_relations_idempotence_and_audits(backend)


def test_images_with_minimal_writer(backend):
    admin_tests.test_images_keep_existing_validation_references_media_and_audit(backend)


@pytest.mark.parametrize("operation", ["highlights", "description", "product_availability", "variant_availability", "tag", "attach", "remove", "upload", "order", "delete"])
def test_each_mutation_rolls_back_with_its_audit_under_writer(backend, operation):
    admin_tests.test_audit_failure_rolls_back_every_real_mutation_and_image_side_effects(backend, operation)


def test_login_under_writer(backend):
    auth_tests.test_login_persists_only_hashes_and_cookie_attributes_and_me(backend)


def test_activity_under_writer(backend):
    auth_tests.test_activity_renewal_is_controlled_and_never_extends_absolute(backend)


def test_logout_all_and_password_change_under_writer(backend):
    b = backend
    response = login(b)
    assert b.client.post("/api/auth/logout-all", headers=csrf_headers(response), json={}).status_code == 204
    response = login(b)
    assert b.client.post("/api/auth/change-password", headers=csrf_headers(response), json={
        "current_password": auth_tests.PASSWORD, "new_password": auth_tests.NEW_PASSWORD}).status_code == 204
    assert b.client.get("/api/auth/me").status_code == 401
    response = login(b, password=auth_tests.NEW_PASSWORD)
    assert response.status_code == 200
    assert b.client.post("/api/auth/logout", headers=csrf_headers(response), json={}).status_code == 204


def test_public_endpoints_never_borrow_administrative_pool(backend, monkeypatch):
    b = backend
    assert login(b).status_code == 200

    def forbidden(*args, **kwargs):
        pytest.fail("Una ruta pública intentó usar la conexión administrativa")

    event.listen(b.app.state.auth_session_factory.kw["bind"], "before_cursor_execute", forbidden)
    b.observed.clear()
    try:
        assert b.client.get("/api/menu").status_code == 200
        assert b.client.get(f"/api/menu/{b.product_id}").status_code == 200
    finally:
        event.remove(b.app.state.auth_session_factory.kw["bind"], "before_cursor_execute", forbidden)
    assert b.observed and {row[0] for row in b.observed} == {"reader"}
    assert all(row[1].lstrip().upper().startswith("SELECT") for row in b.observed)


FORBIDDEN = [
    'UPDATE "Products" SET loyverse_id = \'forbidden\'',
    'UPDATE "Products" SET is_active = false',
    'UPDATE "Products" SET name = \'forbidden\'',
    'UPDATE "Products" SET synced_at = now()',
    'UPDATE "Products" SET is_featured = true, name = \'forbidden\'',
    'UPDATE "Products" SET is_new = false, is_active = true',
    'UPDATE "ProductVariants" SET price = 0',
    'UPDATE "ProductVariants" SET loyverse_id = \'forbidden\'',
    'UPDATE "ProductVariants" SET is_commercially_available = false',
    'UPDATE "ProductVariants" SET product_id = 1',
    'DELETE FROM "Products"', 'DELETE FROM "ProductVariants"',
    'INSERT INTO "Products" DEFAULT VALUES', 'INSERT INTO "ProductVariants" DEFAULT VALUES',
    'UPDATE "ProductModifiers" SET name = \'forbidden\'',
    'UPDATE "ProductComplements" SET price = 0',
    'DELETE FROM "ProductModifierAssignments"',
    'SELECT * FROM "ProductModifiers"',
    'SELECT phone FROM "Persons"',
    'INSERT INTO "Persons" DEFAULT VALUES', 'UPDATE "Persons" SET name = \'forbidden\'',
    'DELETE FROM "PersonalAccounts"', 'INSERT INTO "PersonalAccounts" DEFAULT VALUES',
    'UPDATE "PersonalAccounts" SET email = \'attacker@example.test\'',
    'UPDATE "PersonalAccounts" SET is_active = true',
    'UPDATE "PersonalAccounts" SET person_id = 1',
    'UPDATE "Employees" SET is_active = true', 'DELETE FROM "Employees"',
    'UPDATE "Roles" SET code = \'admin\'',
    'INSERT INTO "EmployeeRoles" VALUES (1, 1)', 'DELETE FROM "EmployeeRoles"',
    'UPDATE "EmployeeRoles" SET role_id = 1',
    'UPDATE "AccountSessions" SET account_id = 1',
    'UPDATE "AccountSessions" SET expires_at = now()',
    'UPDATE "AccountSessions" SET token_hash = repeat(\'a\', 64)',
    'DELETE FROM "AccountSessions"',
    'UPDATE "AuditEvents" SET outcome = \'success\'', 'DELETE FROM "AuditEvents"',
    'SELECT action FROM "AuditEvents"',
    'INSERT INTO "AuditEvents" (audit_event_id, action, outcome) VALUES (100, \'fake\', \'success\')',
    'INSERT INTO "AuditEvents" (action, outcome, created_at) VALUES (\'fake\', \'success\', now())',
    'UPDATE "Tags" SET name = \'forbidden\'', 'DELETE FROM "Tags"',
    'UPDATE "ProductImages" SET image_url = \'/media/forbidden\'',
    'UPDATE "ProductImages" SET product_variant_id = 1',
    'UPDATE "ProductTags" SET tag_id = 1',
    'TRUNCATE "ProductTags"',
    'SELECT nextval(\'"Products_product_id_seq"\')',
    'SELECT setval(\'"Tags_tag_id_seq"\', 1)',
    'CREATE TABLE public.forbidden (id int)', 'CREATE TEMP TABLE forbidden (id int)',
    'CREATE SCHEMA forbidden', 'CREATE DATABASE forbidden',
    'ALTER TABLE "Products" ADD COLUMN forbidden int', 'DROP TABLE "ProductImages"',
    'CREATE INDEX forbidden ON "Products" (name)',
    'CREATE FUNCTION public.forbidden() RETURNS int LANGUAGE sql AS \'SELECT 1\'',
    'CREATE ROLE forbidden', 'ALTER ROLE mitbol_admin_writer SUPERUSER',
    'GRANT mitbol_ci TO mitbol_admin_writer', 'SET ROLE mitbol_ci',
    'UPDATE alembic_version SET version_num = \'forbidden\'', 'SELECT * FROM alembic_version',
]


@pytest.mark.parametrize("statement", FORBIDDEN)
def test_writer_is_denied_by_postgresql_not_only_fastapi(writer_database, statement):
    denied(writer_database.writer, statement)


def test_writer_cannot_delegate_column_privileges(writer_database):
    # GRANT sin grant option puede devolver WARNING sin excepción: comprobar efecto.
    with writer_database.writer.begin() as db:
        db.exec_driver_sql('GRANT UPDATE(description) ON "Products" TO mitbol_render_reader')
    with writer_database.owner.connect() as db:
        assert not db.scalar(text("SELECT has_column_privilege('mitbol_render_reader', '\"Products\"', 'description', 'UPDATE')"))


@pytest.mark.parametrize("statement,code", [
    ('INSERT INTO "ProductImages" (product_variant_id,image_url,display_order) VALUES (2147483647,\'/media/test\',0)', "23503"),
    ('INSERT INTO "ProductTags" (product_variant_id,tag_id) VALUES (2147483647,2147483647)', "23503"),
    ('INSERT INTO "AuditEvents" (actor_account_id,action,outcome) VALUES (2147483647,\'test\',\'success\')', "23503"),
    ('INSERT INTO "AuditEvents" (action,outcome) VALUES (\'test\',\'invalid\')', "23514"),
    ('INSERT INTO "AccountSessions" (session_id,account_id,token_hash,auth_version,created_at,last_activity_at,expires_at,idle_expires_at) '
     'VALUES (gen_random_uuid(),2147483647,repeat(\'a\',64),0,now(),now(),now()+interval \'8 hours\',now()+interval \'30 minutes\')', "23503"),
    ('INSERT INTO "AccountSessions" (session_id,account_id,token_hash,auth_version,created_at,last_activity_at,expires_at,idle_expires_at) '
     'VALUES (gen_random_uuid(),2147483647,\'invalid\',0,now(),now(),now()+interval \'8 hours\',now()+interval \'30 minutes\')', "23514"),
])
def test_constraints_still_apply_to_writer(writer_database, statement, code):
    denied(writer_database.writer, statement, code=code)


def test_verifier_detects_a_dangerous_column_grant_and_public_leak(writer_database):
    r = writer_database
    for role in ("mitbol_admin_writer", "PUBLIC"):
        with r.owner.begin() as db:
            db.exec_driver_sql(f'GRANT UPDATE(price) ON "ProductVariants" TO {role}')
        try:
            with pytest.raises(DBAPIError, match="WRITER_COLUMN"):
                execute_script(r.writer, VERIFY.read_text(encoding="utf-8"))
        finally:
            with r.owner.begin() as db:
                db.exec_driver_sql(f'REVOKE UPDATE(price) ON "ProductVariants" FROM {role}')
    execute_script(r.writer, VERIFY.read_text(encoding="utf-8"))


def test_writer_cannot_run_alembic(backend):
    b = backend
    # La revisión negativa sucede antes de cualquier DDL si no puede leer versión.
    with b.roles.writer.connect() as db:
        with pytest.raises(CommandError):
            command.upgrade(config_for(db), "head")
        db.rollback()
    with b.roles.owner.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261106_0005"


def test_role_change_uses_existing_account_lock_and_invalidates_pending_request(backend, monkeypatch):
    b = backend
    headers = csrf_headers(login(b))
    updated, release, attempting = Event(), Event(), Event()

    def change_access():
        with b.sessions.begin() as db:
            account = repo.lock_account(db, b.ids["admin"][0])
            db.execute(delete(EmployeeRole).where(EmployeeRole.person_id == account.person_id))
            repo.invalidate_locked_account(db, account, b.now.value, "synthetic_role_change")
            updated.set()
            assert release.wait(5)

    def request():
        attempting.set()
        return b.client.patch(admin_tests.product_path(b), headers=headers, json={"description": "Forbidden"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = pool.submit(change_access)
        assert updated.wait(5)
        response = pool.submit(request)
        try:
            assert attempting.wait(5)
        finally:
            release.set()
        changed.result(timeout=10)
        assert response.result(timeout=10).status_code == 401
    assert not admin_tests.audit_rows(b)
    with b.sessions() as db:
        assert db.get(Product, b.product_id).description == "Original"


def test_in_flight_admin_mutation_serializes_with_revocation(backend, monkeypatch):
    admin_tests.test_mutation_and_security_revocation_serialize_on_account_lock(backend, monkeypatch)

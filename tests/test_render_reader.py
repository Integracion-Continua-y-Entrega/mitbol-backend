"""Scripts del lector en PostgreSQL temporal; sin migraciones ni sincronizaciones."""

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.connection import READ_ONLY_ROLE_SQL
from app.db.database import get_db
from app.main import create_app
from app.models import AuthBase, Base, Product, ProductVariant, ProductModifier, ProductComplement, ProductModifierAssignment


ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "docs/database/prepare_render_reader.sql"
AUDIT_FILE = ROOT / "docs/database/verify_neon_stage3c.sql"
VERIFY_FILE = ROOT / "docs/database/verify_render_reader.sql"


@pytest.fixture(scope="module")
def reader_control():
    """Solo cluster temporal identificado por su data_directory, o servicio CI."""
    cluster = os.getenv("MITBOL_READER_TEST_CLUSTER")
    if cluster:
        directory = Path(cluster).resolve()
        import tempfile
        assert directory.parent.parent == Path(tempfile.gettempdir()).resolve()
        assert re.fullmatch(r"mitbol-reader-hardening-[a-f0-9]{32}", directory.parent.name)
        assert directory.name == "data"
        port = int(os.environ["MITBOL_READER_TEST_PORT"])
        assert 1024 <= port <= 65535
        url = URL.create("postgresql+psycopg2", username="mitbol_ci",
                         host="127.0.0.1", port=port, database="postgres")
    elif os.getenv("GITHUB_ACTIONS") == "true":
        url = make_url(os.environ["DATABASE_URL"])
        assert (url.drivername, url.host, url.username, url.database, dict(url.query)) == (
            "postgresql+psycopg2", "127.0.0.1", "mitbol_ci", "mitbol_test_ci", {})
        directory = None
    else:
        pytest.skip("Requiere cluster temporal dedicado; nunca carga .env ni usa mitbol_dev")
    engine = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True,
                           connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == "mitbol_ci"
        assert int(connection.scalar(text("SHOW server_version_num"))) // 10000 == 18
        if directory is not None:
            assert Path(connection.scalar(text("SHOW data_directory"))).resolve() == directory
        assert not connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_render_reader')"))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def reader_database(reader_control, tmp_path):
    name = "mitbol_test_reader_" + uuid4().hex[:12]
    quoted = reader_control.dialect.identifier_preparer.quote
    with reader_control.connect() as admin:
        admin.exec_driver_sql(f"CREATE DATABASE {quoted(name)} TEMPLATE template0")
    engine = create_engine(reader_control.url.set(database=name), hide_parameters=True)
    auxiliary_roles = []

    def sql_file(path):
        return path.read_text(encoding="utf-8").replace("mitbol_cloud_dev", name).replace("neondb_owner", "mitbol_ci")

    def execute_script(path, transform=lambda value: value):
        # AUTOCOMMIT permite que BEGIN/COMMIT/ROLLBACK del archivo sean los reales.
        sql = transform(sql_file(path))
        sql = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            try:
                connection.exec_driver_sql(sql)
            except Exception:
                connection.exec_driver_sql("ROLLBACK")
                raise

    def psql_verify(*, user="mitbol_render_reader", transform=lambda value: value, filename=VERIFY_FILE):
        executable = os.getenv("MITBOL_READER_TEST_PSQL", "psql")
        script = tmp_path / ("verify_" + uuid4().hex + ".sql")
        script.write_text(transform(sql_file(filename)), encoding="utf-8")
        process_env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
        # Solo credencial efimera del servicio CI; localmente se usa trust en loopback.
        if reader_control.url.password:
            process_env["PGPASSWORD"] = ("mitbol-reader-fixture" if user == "mitbol_render_reader"
                                         else reader_control.url.password)
        return subprocess.run(
            [executable, "-X", "-w", "-h", "127.0.0.1", "-p", str(engine.url.port or 5432),
             "-U", user, "-d", name, "-v", "ON_ERROR_STOP=1", "-P", "pager=off", "-f", str(script)],
            env=process_env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )

    try:
        with engine.begin() as connection:
            # Fixture sintetica, NO Alembic, NO llamadas a SyncService/Loyverse.
            Base.metadata.create_all(connection)
            connection.exec_driver_sql("CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO public.alembic_version VALUES ('20261102_0003')")
        yield SimpleNamespace(engine=engine, name=name, script=sql_file,
                              execute_script=execute_script, psql=psql_verify, auxiliary_roles=auxiliary_roles)
    finally:
        engine.dispose()
        with reader_control.connect() as admin:
            admin.exec_driver_sql(f"DROP DATABASE {quoted(name)} WITH (FORCE)")
            admin.exec_driver_sql("DROP ROLE IF EXISTS mitbol_render_reader")
            for role in auxiliary_roles:
                assert role.startswith("mitbol_reader_aux_")
                admin.exec_driver_sql(f"DROP ROLE IF EXISTS {quoted(role)}")


def create_reader(database):
    database.execute_script(SQL_FILE)
    if database.engine.url.password:
        with database.engine.begin() as connection:
            connection.exec_driver_sql("ALTER ROLE mitbol_render_reader PASSWORD 'mitbol-reader-fixture'")


@pytest.fixture
def reader(reader_database):
    database = reader_database
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with database.engine.begin() as connection:
        for index in range(1, 4):
            connection.execute(insert(Product).values(
                product_id=index, loyverse_id=f"test-product-{index}", name=f"Producto {index}", synced_at=now))
            connection.execute(insert(ProductVariant).values(
                product_variant_id=index, product_id=index, loyverse_id=f"test-variant-{index}",
                name="Presentacion", price=10, synced_at=now, is_commercially_available=True))
        connection.execute(insert(ProductModifier).values(
            product_modifier_id=1, loyverse_id="test-modifier", name="Extras", display_order=1))
        connection.execute(insert(ProductComplement).values(
            product_complement_id=1, product_modifier_id=1, loyverse_id="test-complement",
            name="Extra", price=2, synced_at=now, display_order=1))
        connection.execute(insert(ProductModifierAssignment).values(product_variant_id=1, product_modifier_id=1))
    create_reader(database)
    with database.engine.connect() as connection:
        transaction = connection.begin()
        sessions = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield SimpleNamespace(engine=database.engine, connection=connection, sessions=sessions, database=database)
        finally:
            transaction.rollback()


def test_reader_has_only_required_catalog_selects_and_no_privileged_memberships(reader):
    connection = reader.connection
    connection.exec_driver_sql("SET LOCAL ROLE mitbol_render_reader")
    try:
        assert connection.exec_driver_sql(READ_ONLY_ROLE_SQL).scalar_one() is False
        assert connection.exec_driver_sql("SELECT has_database_privilege(current_user, current_database(), 'TEMPORARY')").scalar_one() is False
        assert connection.exec_driver_sql('SELECT count(*) FROM "Products"').scalar_one() == 3
        assert connection.exec_driver_sql("SELECT has_table_privilege(current_user, 'alembic_version', 'SELECT')").scalar_one() is False
        assert connection.exec_driver_sql("SELECT count(*) FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname = current_user)").scalar_one() == 0
    finally:
        connection.exec_driver_sql("RESET ROLE")


def test_reader_cannot_read_new_identity_or_security_tables(reader_database):
    database = reader_database
    with database.engine.begin() as connection:
        AuthBase.metadata.create_all(connection)
    create_reader(database)
    with database.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL ROLE mitbol_render_reader")
        for name in AuthBase.metadata.tables:
            assert not connection.scalar(text(
                "SELECT has_table_privilege(current_user, :relation, 'SELECT')"
            ), {"relation": f'public."{name}"'})
        assert connection.exec_driver_sql(READ_ONLY_ROLE_SQL).scalar_one() is False
    with database.engine.connect() as connection:
        connection.exec_driver_sql("SET ROLE mitbol_render_reader")
        with pytest.raises(ProgrammingError):
            connection.exec_driver_sql('SELECT password_hash FROM "PersonalAccounts"')
        connection.rollback()


@pytest.mark.parametrize("statement", [
    'INSERT INTO "Tags" (name) VALUES (\'No autorizado\')',
    'UPDATE "Products" SET name = \'No autorizado\'',
    'DELETE FROM "Products"',
    'CREATE TABLE public.unauthorized (id integer)',
    'CREATE TEMP TABLE unauthorized_temp (id integer)',
    'CREATE SCHEMA unauthorized_schema',
    'ALTER TABLE "Products" ADD COLUMN unauthorized integer',
    'DROP TABLE "ProductImages"',
])
def test_reader_cannot_write_or_execute_ddl(reader, statement):
    connection = reader.connection
    connection.exec_driver_sql("SET LOCAL ROLE mitbol_render_reader")
    try:
        with pytest.raises(ProgrammingError) as error:
            with connection.begin_nested():
                connection.exec_driver_sql(statement)
        assert error.value.orig.pgcode == "42501"
    finally:
        connection.exec_driver_sql("RESET ROLE")


def test_future_tables_require_explicit_reviewed_read_grants(reader):
    connection = reader.connection
    connection.exec_driver_sql('CREATE TABLE public."FuturePrivate" (value text)')
    connection.exec_driver_sql("SET LOCAL ROLE mitbol_render_reader")
    try:
        with pytest.raises(ProgrammingError) as error:
            with connection.begin_nested():
                connection.exec_driver_sql('SELECT * FROM public."FuturePrivate"')
        assert error.value.orig.pgcode == "42501"
    finally:
        connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql('GRANT SELECT ON public."FuturePrivate" TO mitbol_render_reader')
    connection.exec_driver_sql("SET LOCAL ROLE mitbol_render_reader")
    try:
        assert connection.exec_driver_sql('SELECT * FROM public."FuturePrivate"').all() == []
    finally:
        connection.exec_driver_sql("RESET ROLE")


def test_public_menu_detail_and_media_need_no_write_privileges(reader):
    connection = reader.connection
    product_id = connection.scalar(select(Product.product_id).order_by(Product.product_id))
    config = Settings(_env_file=None, app_env="cloud", loyverse_access_token=None,
                      database_url="postgresql://reader:fake@ep-reader.example.com/mitbol_cloud_dev?sslmode=verify-full",
                      expected_database="mitbol_cloud_dev", expected_database_host="ep-reader.example.com",
                      cors_origins=["http://localhost:5173"], admin_enabled=False)
    application = create_app(config)

    def isolated_reader():
        with reader.sessions() as session:
            yield session

    application.dependency_overrides[get_db] = isolated_reader
    statements = []

    def observe(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    connection.exec_driver_sql("SET LOCAL ROLE mitbol_render_reader")
    event.listen(connection, "before_cursor_execute", observe)
    try:
        with TestClient(application) as client:
            menu = client.get("/api/menu")
            assert menu.status_code == 200 and len(menu.json()["products"]) == 3
            assert all(product["image_url"] is None for product in menu.json()["products"])
            detail = client.get(f"/api/menu/{product_id}")
            assert detail.status_code == 200
            assert all(variant["images"] == [] for variant in detail.json()["product"]["variants"])
            variant_id = detail.json()["product"]["variants"][0]["product_variant_id"]
            assert client.get(f"/media/products/{product_id}/variants/{variant_id}/{'0' * 32}.png").status_code == 404
        assert all(sql.lstrip().upper().startswith(("SELECT", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")) for sql in statements)
    finally:
        event.remove(connection, "before_cursor_execute", observe)
        connection.exec_driver_sql("RESET ROLE")


# Escrituras de estas pruebas: exclusivamente en el cluster temporal validado.
UNSAFE_GRANTS = [
    ('GRANT INSERT ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT UPDATE ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT DELETE ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT TRUNCATE ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT REFERENCES ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT TRIGGER ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT MAINTAIN ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT INSERT (name) ON public."Tags" TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT UPDATE (name) ON public."Tags" TO PUBLIC', "TABLE_OR_COLUMN"),
    ('GRANT REFERENCES (name) ON public."Tags" TO PUBLIC', "TABLE_OR_COLUMN"),
    ('GRANT SELECT ON public.alembic_version TO mitbol_render_reader', "TABLE_OR_COLUMN"),
    ('GRANT SELECT (version_num) ON public.alembic_version TO PUBLIC', "TABLE_OR_COLUMN"),
    ('CREATE SCHEMA private; CREATE TABLE private.secret (value text); '
     'GRANT SELECT (value) ON private.secret TO PUBLIC', "TABLE_OR_COLUMN"),
    ('CREATE VIEW public.secret AS SELECT 1 AS value; GRANT SELECT ON public.secret TO PUBLIC', "TABLE_OR_COLUMN"),
    ('GRANT SELECT ON SEQUENCE public."Tags_tag_id_seq" TO mitbol_render_reader', "SEQUENCE"),
    ('GRANT USAGE ON SEQUENCE public."Tags_tag_id_seq" TO PUBLIC', "SEQUENCE"),
    ('GRANT UPDATE ON SEQUENCE public."Tags_tag_id_seq" TO PUBLIC', "SEQUENCE"),
    ('GRANT CREATE ON DATABASE TARGET_DATABASE TO mitbol_render_reader', "CREATE_OR_TEMP"),
    ('GRANT TEMPORARY ON DATABASE TARGET_DATABASE TO PUBLIC', "CREATE_OR_TEMP"),
    ('CREATE SCHEMA private; GRANT CREATE ON SCHEMA private TO PUBLIC', "CREATE_OR_TEMP"),
    ('GRANT SELECT ON public."Products" TO mitbol_render_reader WITH GRANT OPTION', "GRANT_OPTION"),
    ('GRANT SELECT (name) ON public."Tags" TO mitbol_render_reader WITH GRANT OPTION', "GRANT_OPTION"),
    ('GRANT USAGE ON SCHEMA public TO mitbol_render_reader WITH GRANT OPTION', "GRANT_OPTION"),
    ('GRANT CONNECT ON DATABASE TARGET_DATABASE TO mitbol_render_reader WITH GRANT OPTION', "GRANT_OPTION"),
    ('CREATE FUNCTION public.helper() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$; '
     'GRANT EXECUTE ON FUNCTION public.helper() TO mitbol_render_reader WITH GRANT OPTION', "GRANT_OPTION"),
    ('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC', "UNSAFE_DEFAULT_ACL"),
    ('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT INSERT ON TABLES TO PUBLIC', "UNSAFE_DEFAULT_ACL"),
    ('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO mitbol_render_reader', "UNSAFE_DEFAULT_ACL"),
    ('ALTER DEFAULT PRIVILEGES GRANT USAGE ON SEQUENCES TO PUBLIC', "UNSAFE_DEFAULT_ACL"),
    ('ALTER DEFAULT PRIVILEGES GRANT CREATE ON SCHEMAS TO PUBLIC', "UNSAFE_DEFAULT_ACL"),
    ('CREATE FUNCTION public.elevated() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$', "UNREVIEWED_DEFINER"),
    ('CREATE FUNCTION public.owned() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$; '
     'ALTER FUNCTION public.owned() OWNER TO mitbol_render_reader', "OBJECT_OWNER"),
    ('ALTER SEQUENCE public."Tags_tag_id_seq" OWNER TO mitbol_render_reader', None),
    ('ALTER TABLE public."Tags" OWNER TO mitbol_render_reader', "OBJECT_OWNER"),
    ('CREATE SCHEMA private AUTHORIZATION mitbol_render_reader', None),
    ('ALTER DATABASE TARGET_DATABASE OWNER TO mitbol_render_reader', None),
    ('GRANT mitbol_ci TO mitbol_render_reader', "MEMBERSHIP"),
    ('ALTER ROLE mitbol_render_reader CREATEDB', "ADMIN_ATTRIBUTES"),
    ('ALTER ROLE mitbol_render_reader CREATEROLE', "ADMIN_ATTRIBUTES"),
    ('ALTER ROLE mitbol_render_reader SUPERUSER', "ADMIN_ATTRIBUTES"),
    ('ALTER ROLE mitbol_render_reader REPLICATION', "ADMIN_ATTRIBUTES"),
    ('ALTER ROLE mitbol_render_reader BYPASSRLS', "ADMIN_ATTRIBUTES"),
    ('ALTER ROLE mitbol_render_reader INHERIT', "ADMIN_ATTRIBUTES"),
]


@pytest.mark.parametrize("statement,expected", UNSAFE_GRANTS)
@pytest.mark.parametrize("phase", ["before_commit", "fresh_login"])
def test_unsafe_privileges_are_rejected(reader_database, statement, expected, phase):
    database = reader_database
    statement = statement.replace("TARGET_DATABASE", database.name)
    # Una secuencia SERIAL asociada no admite ALTER OWNER individual; usar una
    # secuencia libre permite comprobar propiedad sin cambiar los modelos.
    if statement.startswith("ALTER SEQUENCE"):
        statement = "CREATE SEQUENCE public.private_sequence; ALTER SEQUENCE public.private_sequence OWNER TO mitbol_render_reader"
        expected = "OBJECT_OWNER"
    if phase == "before_commit":
        with database.engine.connect() as connection:
            before = connection.exec_driver_sql(
                "SELECT datacl::text FROM pg_database WHERE datname=current_database()").scalar_one()
        marker = "-- Misma politica"
        with pytest.raises(DBAPIError) as error:
            database.execute_script(SQL_FILE, lambda sql: sql.replace(marker, statement + ";\n" + marker))
        assert "READER_" in str(error.value.orig)
        if expected:
            assert "READER_" + expected in str(error.value.orig)
        with database.engine.connect() as connection:
            assert not connection.exec_driver_sql(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_render_reader')").scalar_one()
            assert connection.exec_driver_sql(
                "SELECT datacl::text FROM pg_database WHERE datname=current_database()").scalar_one() == before
    else:
        create_reader(database)
        with database.engine.begin() as connection:
            connection.exec_driver_sql(statement)
        result = database.psql()
        assert result.returncode != 0, result.stdout[-1500:]
        assert "READER_" in result.stderr, result.stderr[-1500:]
        if expected:
            assert "READER_" + expected in result.stderr, result.stderr[-1500:]


@pytest.mark.parametrize("read_only_spelling", ["on", "true"])
def test_fresh_login_passes_and_normal_system_execute_is_not_a_failure(reader_database, read_only_spelling):
    create_reader(reader_database)
    with reader_database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER ROLE mitbol_render_reader SET default_transaction_read_only = " + read_only_spelling)
    result = reader_database.psql()
    assert result.returncode == 0, result.stderr[-1500:]
    assert "reader_permissions_verified" in result.stdout
    assert "lo_create" in result.stdout and "lo_from_bytea" in result.stdout
    assert "POTENCIAL_NO_EJECUTADO" in result.stdout


@pytest.mark.parametrize("change,expected", [
    ("ALTER ROLE mitbol_render_reader SET default_transaction_read_only=off", "READ_ONLY_CONFIGURATION"),
    ('REVOKE SELECT ON public."Products" FROM mitbol_render_reader', "REQUIRED_SELECT"),
    ("REVOKE USAGE ON SCHEMA public FROM PUBLIC, mitbol_render_reader", "REQUIRED_ACCESS"),
])
def test_missing_required_access_and_default_read_only_fail(reader_database, change, expected):
    create_reader(reader_database)
    with reader_database.engine.begin() as connection:
        connection.exec_driver_sql(change)
    result = reader_database.psql()
    assert result.returncode != 0
    assert "READER_" + expected in result.stderr, result.stderr[-1500:]


def test_verification_rejects_wrong_identity_and_read_write_transaction(reader_database):
    create_reader(reader_database)
    wrong_user = reader_database.psql(user="mitbol_ci")
    assert wrong_user.returncode != 0 and "READER_IDENTITY" in wrong_user.stderr
    wrong_mode = reader_database.psql(transform=lambda sql: sql.replace(
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;",
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE;"))
    assert wrong_mode.returncode != 0 and "READER_READ_ONLY_CONFIGURATION" in wrong_mode.stderr


def test_pre_audit_parses_and_runs_without_creating_objects(reader_database):
    before = reader_database.psql(user="mitbol_ci", filename=AUDIT_FILE)
    assert before.returncode == 0, before.stderr[-1500:]
    assert "POTENCIAL_NO_EJECUTADO" in before.stdout
    assert "NO conecta" in before.stdout or "no tablas/rutinas" in before.stdout
    with reader_database.engine.connect() as connection:
        assert not connection.exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_render_reader')").scalar_one()
        assert connection.exec_driver_sql('SELECT count(*) FROM public."Products"').scalar_one() == 0
    create_reader(reader_database)
    repeated = reader_database.psql(user="mitbol_ci", filename=AUDIT_FILE)
    assert repeated.returncode != 0 and "READER_ALREADY_EXISTS" in repeated.stderr


def test_defaults_of_system_functions_and_other_database_acl_remain_unchanged(reader_database, reader_control):
    with reader_control.connect() as connection:
        before = connection.exec_driver_sql(
            "SELECT datname, datacl::text FROM pg_database WHERE datname='postgres'").all()
    with reader_database.engine.begin() as connection:
        connection.exec_driver_sql("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")
    create_reader(reader_database)
    with reader_control.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT datname, datacl::text FROM pg_database WHERE datname='postgres'").all() == before
    assert reader_database.psql().returncode == 0


def test_guard_rejects_wrong_database_and_revision_before_creating_role(reader_database):
    with pytest.raises(DBAPIError):
        reader_database.execute_script(SQL_FILE, lambda sql: sql.replace(
            "current_database() <> '" + reader_database.name + "'", "current_database() <> 'wrong_database'"))
    with reader_database.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE public.alembic_version SET version_num='wrong_revision'")
    with pytest.raises(DBAPIError):
        create_reader(reader_database)
    with reader_database.engine.connect() as connection:
        assert not connection.exec_driver_sql(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mitbol_render_reader')").scalar_one()


def test_verifiers_do_not_invoke_side_effect_functions_or_contain_write_commands():
    for path in (AUDIT_FILE, VERIFY_FILE):
        source = path.read_text(encoding="utf-8")
        assert "READ ONLY;" in source and "\nROLLBACK;" in source
        # Quita comentarios/literales para distinguir nombres auditados de llamadas.
        stripped = re.sub(r"--[^\n]*|'(?:[^']|'')*'", "", source)
        assert not re.search(r"^\s*(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|GRANT|REVOKE|TRUNCATE|COPY|CALL)\b",
                             stripped, re.M | re.I)
        assert not re.search(r"\b(lo_create|lo_creat|lo_from_bytea|lo_put|lowrite|nextval|setval)\s*\(",
                             stripped, re.I)

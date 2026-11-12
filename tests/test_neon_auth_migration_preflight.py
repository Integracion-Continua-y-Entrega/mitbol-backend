"""Preflight/0003->0004->0005 con datos sinteticos y lector anterior a las migraciones."""

from app.models import catalog_metadata
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from alembic import command, op
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.database import get_db
from app.main import create_app
from app.models import AuthBase, Base
from test_auth_persistence import config_for, AUTH_TABLES, RATE_TABLES
from test_admin_writer import execute_script, denied
from test_cloud_configuration import cloud_settings

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "docs/database/neon_auth_migration_preflight.sql"
PREVIOUS, CORE, HEAD = "20261102_0003", "20261105_0004", "20261106_0005"
TLS_CHECK = "AND coalesce((SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid = pg_catalog.pg_backend_pid()), false)"


@pytest.fixture
def migration_lab(request, tmp_path):
    # Rechazar la ruta local de conftest ANTES de solicitar su fixture: esa ruta
    # optativa consulta .env/mitbol_dev, que no pertenecen a este laboratorio.
    if not os.getenv("MITBOL_SYNC_TEST_CLUSTER") and os.getenv("GITHUB_ACTIONS") != "true":
        pytest.skip("Requiere cluster temporal identificado o servicio CI autorizado; nunca .env")
    source = request.getfixturevalue("sync_test_database").url
    assert source.host == "127.0.0.1" and source.username == "mitbol_ci"
    assert source.database.startswith("mitbol_test_hu008_")
    control = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = "mitbol_test_auth_" + uuid4().hex
    quoted = control.dialect.identifier_preparer.quote(name)
    with control.connect() as db:
        assert db.exec_driver_sql("SELECT current_user, session_user").one() == ("mitbol_ci",) * 2
        assert int(db.exec_driver_sql("SHOW server_version_num").scalar_one()) // 10000 == 18
        for role in ("mitbol_render_reader", "mitbol_admin_writer"):
            assert not db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": role})
        db.exec_driver_sql(f"CREATE DATABASE {quoted} TEMPLATE template0 ENCODING 'UTF8'")
    owner = create_engine(source.set(database=name), hide_parameters=True)
    engines = []
    created_roles = []
    try:
        with owner.begin() as db:
            command.upgrade(config_for(db), PREVIOUS)
            now = datetime(2026, 11, 6, tzinfo=timezone.utc)
            tables = catalog_metadata.tables
            product = db.scalar(tables["Products"].insert().values(loyverse_id="synthetic-product", name="Catalogo sintetico", description="Conservar", synced_at=now).returning(tables["Products"].c.product_id))
            variant = db.scalar(tables["ProductVariants"].insert().values(loyverse_id="synthetic-variant", product_id=product, name="Presentacion", price="129.50", synced_at=now, is_commercially_available=True).returning(tables["ProductVariants"].c.product_variant_id))
            modifier = db.scalar(tables["ProductModifiers"].insert().values(loyverse_id="synthetic-modifier", name="Extras", display_order=1).returning(tables["ProductModifiers"].c.product_modifier_id))
            db.execute(tables["ProductComplements"].insert().values(loyverse_id="synthetic-complement", product_modifier_id=modifier, name="Extra", price="2.25", display_order=1, synced_at=now))
            db.execute(tables["ProductModifierAssignments"].insert().values(product_variant_id=variant, product_modifier_id=modifier))
            tag = db.scalar(tables["Tags"].insert().values(name="Sintetico").returning(tables["Tags"].c.tag_id))
            db.execute(tables["ProductTags"].insert().values(product_variant_id=variant, tag_id=tag))
            db.execute(tables["ProductImages"].insert().values(product_variant_id=variant, image_url=f"/media/products/{product}/variants/{variant}/{'0'*32}.png", display_order=1))
        sql = (ROOT / "docs/database/prepare_render_reader.sql").read_text(encoding="utf-8")
        execute_script(owner, sql.replace("mitbol_cloud_dev", name).replace("neondb_owner", "mitbol_ci"))
        created_roles.append("mitbol_render_reader")
        if source.password:
            with owner.begin() as db:
                db.exec_driver_sql("ALTER ROLE mitbol_render_reader PASSWORD 'mitbol-reader-fixture'")
        reader = create_engine(source.set(database=name, username="mitbol_render_reader", password="mitbol-reader-fixture" if source.password else None), hide_parameters=True)
        engines.append(reader)

        def preflight(revision, *, transform=lambda sql: sql, production_guard=False):
            sql = PREFLIGHT.read_text(encoding="utf-8")
            if not production_guard:
                # Solo esta copia temporal: no existe TLS en initdb trust del laboratorio.
                assert TLS_CHECK in sql
                sql = sql.replace("mitbol_cloud_dev", name).replace("neondb_owner", "mitbol_ci").replace(TLS_CHECK, "AND true")
            path = tmp_path / (uuid4().hex + ".sql")
            path.write_text(transform(sql), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
            if source.password:
                env["PGPASSWORD"] = source.password
            args = [os.getenv("MITBOL_READER_TEST_PSQL", "psql"), "-X", "-w", "-h", "127.0.0.1", "-p", str(source.port or 5432), "-U", "mitbol_ci", "-d", name, "-v", "ON_ERROR_STOP=1"]
            if revision is not None:
                args += ["-v", f"expected_revision={revision}"]
            return subprocess.run([*args, "-f", str(path)], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)

        yield SimpleNamespace(owner=owner, reader=reader, created_roles=created_roles, engines=engines,
                              product=product, preflight=preflight)
    finally:
        for engine in engines:
            engine.dispose()
        owner.dispose()
        with control.connect() as db:
            db.exec_driver_sql(f"DROP DATABASE {quoted} WITH (FORCE)")
            for role in reversed(created_roles):
                db.exec_driver_sql(f"DROP ROLE {role}")
        control.dispose()


def catalog_snapshot(engine):
    with engine.connect() as db:
        inspector = inspect(db)
        rows = {name: db.execute(select(table).order_by(*table.primary_key.columns)).all() for name, table in catalog_metadata.tables.items()}
        schema = {name: (inspector.get_columns(name), inspector.get_pk_constraint(name), inspector.get_foreign_keys(name), inspector.get_unique_constraints(name), inspector.get_check_constraints(name), inspector.get_indexes(name)) for name in catalog_metadata.tables}
        # Reflected SQL type objects compare by identity: canonicalize their strings.
        for columns, *_ in schema.values():
            for column in columns:
                column["type"] = str(column["type"])
        sequences = {}
        for name in catalog_metadata.tables:
            for column in catalog_metadata.tables[name].primary_key:
                sequence = db.scalar(text("SELECT pg_get_serial_sequence(:table_name,:column_name)"), {"table_name": f'public."{name}"', "column_name": column.name})
                if sequence:
                    sequences[sequence] = tuple(db.exec_driver_sql(f"SELECT last_value,is_called FROM {sequence}").one())
        return rows, schema, sequences


def public_response(lab, tmp_path):
    app = create_app(cloud_settings(auth_enabled=False, admin_enabled=False, image_storage_root=tmp_path))
    sessions = sessionmaker(bind=lab.reader)
    def reader_session():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = reader_session
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        menu, detail = client.get("/api/menu"), client.get(f"/api/menu/{lab.product}")
        assert menu.status_code == detail.status_code == 200
        assert client.post("/api/auth/login", json={}).status_code == 404
        return menu.json(), detail.json()


def test_preexisting_reader_catalog_and_models_survive_each_migration(migration_lab, tmp_path):
    lab = migration_lab
    baseline, response = catalog_snapshot(lab.owner), public_response(lab, tmp_path)
    assert all(len(rows) == 1 for rows in baseline[0].values())
    result = lab.preflight(PREVIOUS)
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("CATALOG_FINGERPRINT") == 8
    for revision, tables in ((CORE, AUTH_TABLES), (HEAD, AUTH_TABLES | RATE_TABLES)):
        with lab.owner.begin() as db:
            command.upgrade(config_for(db), revision)
        assert catalog_snapshot(lab.owner) == baseline
        assert public_response(lab, tmp_path) == response
        result = lab.preflight(revision)
        assert result.returncode == 0, result.stderr
        with lab.owner.connect() as db:
            assert set(inspect(db).get_table_names()) == set(catalog_metadata.tables) | tables | {"alembic_version"}
            assert db.exec_driver_sql('SELECT code FROM "Roles" ORDER BY code').scalars().all() == ["admin", "staff"]
        for table in tables:
            denied(lab.reader, f'SELECT * FROM "{table}"')
        verify = (ROOT / "docs/database/verify_render_reader.sql").read_text(encoding="utf-8")
        execute_script(lab.reader, "\n".join(line for line in verify.replace("mitbol_cloud_dev", lab.owner.url.database).splitlines() if not line.startswith("\\")))
    with lab.owner.connect() as db:
        context = MigrationContext.configure(db, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(context, [catalog_metadata, AuthBase.metadata]) == []
    execute_script(lab.owner, (ROOT / "docs/database/prepare_admin_writer_local.sql").read_text(encoding="utf-8"))
    lab.created_roles.append("mitbol_admin_writer")
    if lab.owner.url.password:
        with lab.owner.begin() as db:
            db.exec_driver_sql("ALTER ROLE mitbol_admin_writer PASSWORD 'mitbol-writer-fixture'")
    writer = create_engine(lab.owner.url.set(username="mitbol_admin_writer", password="mitbol-writer-fixture" if lab.owner.url.password else None), hide_parameters=True)
    lab.engines.append(writer)
    execute_script(writer, (ROOT / "docs/database/verify_admin_writer_local.sql").read_text(encoding="utf-8"))
    denied(writer, 'UPDATE "ProductVariants" SET price=0')
    denied(writer, 'UPDATE "Products" SET loyverse_id=\'forbidden\'')
    denied(writer, 'UPDATE "ProductVariants" SET loyverse_id=\'forbidden\'')
    assert catalog_snapshot(lab.owner) == baseline


@pytest.mark.parametrize("revision", [CORE, HEAD])
def test_partial_migration_failure_rolls_back_ddl_and_version(migration_lab, monkeypatch, revision):
    lab = migration_lab
    previous = PREVIOUS
    if revision == HEAD:
        with lab.owner.begin() as db:
            command.upgrade(config_for(db), CORE)
        previous = CORE
    baseline = catalog_snapshot(lab.owner)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic index failure")
    with monkeypatch.context() as patch:
        patch.setattr(op, "create_index", fail)
        with pytest.raises(RuntimeError, match="synthetic index failure"), lab.owner.begin() as db:
            command.upgrade(config_for(db), revision)
    with lab.owner.connect() as db:
        assert MigrationContext.configure(db).get_current_heads() == (previous,)
        assert set(inspect(db).get_table_names()) == set(catalog_metadata.tables) | (AUTH_TABLES if previous == CORE else set()) | {"alembic_version"}
    assert catalog_snapshot(lab.owner) == baseline


@pytest.mark.parametrize("mode", ["production_guard", "wrong_revision", "missing_revision", "table_conflict", "index_conflict", "unique_index_conflict", "type_conflict", "search_path"])
def test_readonly_preflight_fails_closed(migration_lab, mode):
    lab = migration_lab
    if mode.endswith("conflict"):
        statement = {"table_conflict": 'CREATE TABLE "Persons" (id int)', "index_conflict": 'CREATE INDEX "ix_AuthRateLimits_expires_at" ON "Products" (name)', "unique_index_conflict": 'CREATE UNIQUE INDEX "uq_AccountSessions_token_hash" ON "Products" (name)', "type_conflict": 'CREATE TYPE "Persons" AS ENUM (\'synthetic\')'}[mode]
        with lab.owner.begin() as db:
            db.exec_driver_sql(statement)
    if mode == "search_path":
        transform = lambda sql: sql.replace("BEGIN TRANSACTION", "SET search_path=pg_catalog;\nBEGIN TRANSACTION")
    else:
        transform = lambda sql: sql
    revision = None if mode == "missing_revision" else CORE if mode == "wrong_revision" else PREVIOUS
    result = lab.preflight(revision, transform=transform, production_guard=mode == "production_guard")
    assert result.returncode != 0
    assert "AUTH_PREFLIGHT" in result.stderr
    with lab.owner.connect() as db:
        assert MigrationContext.configure(db).get_current_heads() == (PREVIOUS,)


def test_downgrade_preserves_catalog_but_deletes_security_records(migration_lab):
    lab = migration_lab
    baseline = catalog_snapshot(lab.owner)
    with lab.owner.begin() as db:
        command.upgrade(config_for(db), HEAD)
        db.exec_driver_sql('INSERT INTO "AuditEvents" (action,outcome) VALUES (\'synthetic.check\',\'success\')')
        db.exec_driver_sql('INSERT INTO "AuthRateLimitPolicy" VALUES (1,repeat(\'a\',64))')
        db.exec_driver_sql('INSERT INTO "AuthRateLimits" VALUES (repeat(\'b\',64),now(),now()+interval \'1 minute\',1)')
    with lab.owner.begin() as db:
        command.downgrade(config_for(db), CORE)
        assert not set(RATE_TABLES) & set(inspect(db).get_table_names())
        assert db.exec_driver_sql('SELECT count(*) FROM "AuditEvents"').scalar_one() == 1
    with lab.owner.begin() as db:
        command.downgrade(config_for(db), PREVIOUS)
        assert not set(AUTH_TABLES) & set(inspect(db).get_table_names())
        command.upgrade(config_for(db), HEAD)
        assert db.exec_driver_sql('SELECT count(*) FROM "AuditEvents"').scalar_one() == 0
        assert db.exec_driver_sql('SELECT count(*) FROM "AuthRateLimits"').scalar_one() == 0
    assert catalog_snapshot(lab.owner) == baseline


def test_preflight_inventories_all_relations_from_the_authentication_models():
    sql = PREFLIGHT.read_text(encoding="utf-8")
    declared = set(re.findall("'([^']+)'", re.search(r"planned CONSTANT text\[\] := ARRAY\[(.*?)\];", sql, re.S).group(1)))
    expected = set()
    for table in AuthBase.metadata.tables.values():
        expected.update((table.name, table.primary_key.name))
        expected.update(index.name for index in table.indexes)
        expected.update(c.name for c in table.constraints if isinstance(c, UniqueConstraint))
        column = table._autoincrement_column
        if column is not None:
            expected.add(f"{table.name}_{column.name}_seq")
    assert declared == expected


def test_inspection_file_is_readonly_and_never_emits_write_statements():
    source = PREFLIGHT.read_text(encoding="utf-8")
    stripped = re.sub(r"--[^\n]*|'(?:[^']|'')*'", "", source)
    assert not re.search(r"\b(CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE|GRANT|REVOKE|COPY|CALL)\b", stripped, re.I)
    assert "READ ONLY" in source and source.rstrip().endswith("ROLLBACK;")


def test_migration_lab_never_requests_the_development_fixture(monkeypatch, tmp_path):
    monkeypatch.delenv("MITBOL_SYNC_TEST_CLUSTER", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    request = MagicMock()
    fixture = migration_lab.__wrapped__(request, tmp_path)
    with pytest.raises(pytest.skip.Exception):
        next(fixture)
    request.getfixturevalue.assert_not_called()

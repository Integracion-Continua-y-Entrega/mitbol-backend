"""Permisos propuestos para Render, probados exclusivamente en PostgreSQL aislado."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import ProgrammingError

from app.core.config import Settings
from app.db.connection import READ_ONLY_ROLE_SQL
from app.db.database import get_db
from app.main import create_app
from app.models import Product


SQL_FILE = Path(__file__).resolve().parents[1] / "docs/database/prepare_render_reader.sql"


@pytest.fixture
def reader(sync_db, commercial_snapshot):
    # Misma propuesta SQL: solo se sustituyen el destino y operador por los de
    # la fixture, y sus BEGIN/COMMIT por el rollback exterior de la prueba.
    assert sync_db.engine.url.host == "127.0.0.1"
    assert sync_db.engine.url.database.startswith("mitbol_test_hu008_")
    sync_db.service.persist_snapshot(commercial_snapshot)
    connection = sync_db.connection
    operator = connection.exec_driver_sql("SELECT current_user").scalar_one()
    assert operator == "mitbol_ci"
    sql = SQL_FILE.read_text(encoding="utf-8")
    assert sql.count("\nBEGIN;\n") == 1 and sql.count("\nCOMMIT;") == 1
    sql = sql.replace("\nBEGIN;\n", "\n", 1).replace("\nCOMMIT;", "\n", 1)
    sql = sql.replace("mitbol_cloud_dev", sync_db.engine.url.database).replace("neondb_owner", operator)
    connection.exec_driver_sql(sql)
    return sync_db


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

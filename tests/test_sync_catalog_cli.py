"""Operación manual con credenciales ficticias; Loyverse siempre está simulado."""

from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from alembic.runtime.migration import MigrationContext
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import event, select, text

from app.cli import sync_catalog
from app.db.connection import CloudDatabaseTarget, DatabaseTargetError
from app.models import Product
from app.services.catalog_errors import CatalogTargetError, CatalogValidationError
from app.services.loyverse_client import LoyverseAPIClient, LoyverseConfigurationError
from app.services.sync_service import SyncService, validate_catalog_revision
from app.services.catalog_manifest import catalog_manifest, manifest_sha256
from app.services.catalog_transform import transform_catalog


HOST = "ep-catalog.example.com"
NAME = "mitbol_cloud_dev"
URL = f"postgresql+psycopg2://operator:PRIVATE_PASSWORD@{HOST}/{NAME}?sslmode=verify-full&channel_binding=require"
ARGS = ["--expected-database", NAME, "--expected-host", HOST]


@pytest.fixture
def cli(monkeypatch, commercial_snapshot):
    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("ADMIN_ENABLED", "false")
    monkeypatch.setenv("EXPECTED_DATABASE", NAME)
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", HOST)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("LOYVERSE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LOYVERSE_STORE_ID", raising=False)
    manifest = catalog_manifest(transform_catalog(commercial_snapshot), commercial_snapshot.store)
    approved = {"status": "ready_for_initial_sync", "writes": False, "database": NAME,
                "host": HOST, "user": "operator", "revision": "20261102_0003",
                "catalog_manifest": manifest, "catalog_sha256": manifest_sha256(manifest),
                "table_counts": {name: 0 for name in sync_catalog.catalog_metadata.tables}}
    monkeypatch.setenv("MITBOL_SYNC_APPROVED_PREFLIGHT", json.dumps(approved))
    engine = MagicMock()
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(sync_catalog, "create_database_engine", factory)
    preflight = MagicMock(return_value={"status": "preflight_ok", "writes": False, "loyverse_contacted": False})
    monkeypatch.setattr(sync_catalog, "preflight", preflight)
    synchronize = AsyncMock(return_value={"status": "synchronized"})
    monkeypatch.setattr(sync_catalog, "synchronize", synchronize)
    preview = AsyncMock(return_value={"store": {"id": "test-store"}, "loyverse_contacted": True})
    monkeypatch.setattr(sync_catalog, "preview_catalog", preview)
    return SimpleNamespace(engine=engine, factory=factory, preflight=preflight,
                           synchronize=synchronize, preview=preview)


@pytest.mark.parametrize("arguments", [ARGS, ARGS + ["--preflight"]])
def test_cli_defaults_to_preflight_without_token_or_store(cli, arguments, capsys):
    assert sync_catalog.main(arguments) == 0
    cli.preflight.assert_called_once()
    cli.synchronize.assert_not_called()
    cli.engine.dispose.assert_called_once()
    output = capsys.readouterr().out
    assert '"writes": false' in output and '"loyverse_contacted": false' in output
    assert "PRIVATE_PASSWORD" not in output


@pytest.mark.parametrize("variable,value", [
    ("APP_ENV", "local"), ("APP_ENV", "production"), ("DATABASE_URL", ""),
    ("ADMIN_ENABLED", "true"), ("ADMIN_ENABLED", ""),
    ("EXPECTED_DATABASE", "other"), ("EXPECTED_DATABASE_HOST", "other.example.com"),
    ("DATABASE_URL", URL.replace(NAME, "mitbol_dev")),
    ("DATABASE_URL", URL.replace("verify-full", "require")),
    ("DATABASE_URL", URL + "&host=other.example.com"),
])
def test_invalid_configuration_never_connects(cli, monkeypatch, capsys, variable, value):
    monkeypatch.setenv(variable, value)
    assert sync_catalog.main(ARGS) == 1
    cli.factory.assert_not_called()
    cli.synchronize.assert_not_called()
    assert "PRIVATE_PASSWORD" not in capsys.readouterr().out


@pytest.mark.parametrize("database,host", [("mitbol_dev", HOST), ("mitbol_test_ci", HOST), (NAME, "localhost")])
def test_forbidden_targets_never_connect(cli, database, host):
    assert sync_catalog.main(["--expected-database", database, "--expected-host", host]) == 1
    cli.factory.assert_not_called()


def test_manual_operations_reject_pooled_endpoint(cli, monkeypatch):
    host = "ep-catalog-pooler.example.com"
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", host)
    monkeypatch.setenv("DATABASE_URL", URL.replace(HOST, host))
    assert sync_catalog.main(["--expected-database", NAME, "--expected-host", host]) == 1
    cli.factory.assert_not_called()


@pytest.mark.parametrize("token,store,argument", [
    (None, "test-store", "test-store"), (" ", "test-store", "test-store"),
    ("private invalid", "test-store", "test-store"),
    ("fake-token", None, "test-store"), ("fake-token", "test-store", None),
    ("fake-token", "different-store", "test-store"),
])
def test_execute_requires_token_and_matching_store_before_connecting(cli, monkeypatch, token, store, argument):
    if token is not None:
        monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", token)
    if store is not None:
        monkeypatch.setenv("LOYVERSE_STORE_ID", store)
    arguments = ARGS + ["--execute"] + (["--store-id", argument] if argument else [])
    assert sync_catalog.main(arguments) == 1
    cli.factory.assert_not_called()
    cli.synchronize.assert_not_called()


def test_explicit_execute_reuses_verified_engine_and_secret_token(cli, monkeypatch, capsys):
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "PRIVATE_TOKEN")
    monkeypatch.setenv("LOYVERSE_STORE_ID", "test-store")
    assert sync_catalog.main(ARGS + ["--execute", "--store-id", "test-store"]) == 0
    cli.preflight.assert_called_once()
    arguments = cli.synchronize.call_args.args
    assert arguments[0] is cli.engine
    assert arguments[2] == "test-store" and isinstance(arguments[3], SecretStr)
    assert arguments[3].get_secret_value() == "PRIVATE_TOKEN"
    output = capsys.readouterr().out
    assert "PRIVATE_TOKEN" not in output and "PRIVATE_PASSWORD" not in output


def test_failed_preflight_never_calls_loyverse_and_disposes_engine(cli, monkeypatch, capsys):
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "PRIVATE_TOKEN")
    monkeypatch.setenv("LOYVERSE_STORE_ID", "test-store")
    cli.preflight.side_effect = RuntimeError("PRIVATE_PASSWORD PRIVATE_TOKEN PRIVATE_SQL")
    assert sync_catalog.main(ARGS + ["--execute", "--store-id", "test-store"]) == 1
    cli.synchronize.assert_not_called()
    cli.engine.dispose.assert_called_once()
    assert "PRIVATE" not in capsys.readouterr().out


def test_cloud_preflight_is_a_real_read_only_transaction(sync_test_database):
    # TLS/host se verifican por separado con el controlador simulado. Aquí se
    # prueba la inspección de revisión y la ausencia de escrituras en PostgreSQL.
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = sync_test_database.url.database
    target.expected_host = "isolated-postgresql"
    statements = []
    with sync_test_database.connect() as connection:
        before = connection.exec_driver_sql(
            "SELECT sequencename, last_value FROM pg_catalog.pg_sequences "
            "WHERE schemaname = 'public' ORDER BY sequencename"
        ).all()

    def observe(connection, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(sync_test_database, "before_cursor_execute", observe)
    try:
        result = sync_catalog.preflight(sync_test_database, target)
    finally:
        event.remove(sync_test_database, "before_cursor_execute", observe)
    assert result["writes"] is False and result["loyverse_contacted"] is False
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert all(statement.lstrip().upper().startswith(("SET TRANSACTION READ ONLY", "SELECT"))
               for statement in statements)
    target.validate_connection.assert_called_once()
    assert len(result["table_counts"]) == 8 and not any(result["table_counts"].values())
    with sync_test_database.connect() as connection:
        assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "off"
        after = connection.exec_driver_sql(
            "SELECT sequencename, last_value FROM pg_catalog.pg_sequences "
            "WHERE schemaname = 'public' ORDER BY sequencename"
        ).all()
    assert after == before


def test_revision_guard_rejects_unmigrated_database_without_creating_tables(empty_postgresql_connection):
    with pytest.raises(CatalogTargetError, match="20261102_0003"):
        validate_catalog_revision(empty_postgresql_connection)
    assert MigrationContext.configure(empty_postgresql_connection).get_current_heads() == ()


@pytest.mark.asyncio
async def test_service_rejects_live_cloud_identity_before_http(sync_db):
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = sync_db.engine.url.database
    target.validate_connection.side_effect = DatabaseTargetError("Destino no autorizado.")
    service = SyncService(sync_db.sessions, expected_database=target.expected_database,
                          store_id="test-store", cloud_target=target)
    client = MagicMock()
    with pytest.raises(CatalogTargetError, match="Destino no autorizado"):
        await service.synchronize(client)
    assert client.mock_calls == []


def test_cloud_path_preserves_local_description_and_rejects_another_store(sync_db, commercial_snapshot):
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = sync_db.engine.url.database
    service = SyncService(sync_db.sessions, expected_database=target.expected_database,
                          store_id="test-store", cloud_target=target)
    service.persist_snapshot(commercial_snapshot)
    with sync_db.sessions() as session:
        product = session.scalar(select(Product).order_by(Product.product_id))
        product.description = "Descripción local conservada"
        session.commit()
    service.persist_snapshot(commercial_snapshot)
    with sync_db.sessions() as session:
        assert session.scalar(select(Product.description).order_by(Product.product_id)) == "Descripción local conservada"
    with pytest.raises(CatalogValidationError):
        service.persist_snapshot(replace(commercial_snapshot, store={"id": "another-store"}))


def test_cloud_service_cannot_use_legacy_authorization(monkeypatch):
    monkeypatch.setenv("APP_ENV", "cloud")
    with pytest.raises(CatalogTargetError, match="autorización explícita"):
        SyncService(MagicMock(), expected_database="mitbol_dev", store_id="test-store")


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_store", [True, False])
async def test_cli_orchestrates_existing_client_and_transactional_service(
    sync_db, commercial_snapshot, monkeypatch, valid_store,
):
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = sync_db.engine.url.database
    calls = []

    def respond(request):
        endpoint = request.url.path.removeprefix("/v1.0")
        calls.append(endpoint)
        assert request.method == "GET"
        if endpoint == "/stores":
            store = commercial_snapshot.store if valid_store else {"id": "another-store", "name": "Otra"}
            return httpx.Response(200, json={"stores": [store]})
        if endpoint == "/items":
            return httpx.Response(200, json={"items": list(commercial_snapshot.items)})
        assert endpoint == "/modifiers"
        return httpx.Response(200, json={"modifiers": list(commercial_snapshot.modifiers)})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(sync_catalog, "LoyverseAPIClient", lambda **kwargs: LoyverseAPIClient(
        **kwargs, transport=transport,
    ))
    # Mantiene todas las escrituras de esta prueba dentro del rollback aislado.
    monkeypatch.setattr(sync_catalog, "sessionmaker", lambda **kwargs: sync_db.sessions)
    if valid_store:
        report = await sync_catalog.synchronize(sync_db.engine, target, "test-store", SecretStr("fake-token"))
        assert report["status"] == "synchronized"
        assert report["result"]["products_processed"] == 3
        assert calls == ["/stores", "/items", "/modifiers"]
        assert len(sync_db.connection.execute(select(Product)).all()) == 3
    else:
        with pytest.raises(LoyverseConfigurationError):
            await sync_catalog.synchronize(sync_db.engine, target, "test-store", SecretStr("fake-token"))
        assert calls == ["/stores"]
        assert sync_db.connection.execute(select(Product)).all() == []


@pytest.mark.asyncio
async def test_cloud_service_checks_revision_before_lock_http_or_upsert(sync_db):
    sync_db.connection.exec_driver_sql("UPDATE alembic_version SET version_num = '20261102_0002'")
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = sync_db.engine.url.database
    service = SyncService(sync_db.sessions, expected_database=target.expected_database,
                          store_id="test-store", cloud_target=target)
    client = MagicMock()
    with pytest.raises(CatalogTargetError, match="20261102_0003"):
        await service.synchronize(client)
    assert client.mock_calls == []
    assert sync_db.connection.execute(select(Product)).all() == []


def test_reader_is_rejected_before_connecting(cli, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL.replace("operator:", "mitbol_render_reader:"))
    assert sync_catalog.main(ARGS) == 1
    cli.factory.assert_not_called()


def test_initial_preflight_never_calls_persistence(cli, monkeypatch, capsys):
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "PRIVATE_TOKEN")
    monkeypatch.setenv("LOYVERSE_STORE_ID", "test-store")
    cli.preflight.return_value["table_counts"] = {"Products": 0}
    assert sync_catalog.main(ARGS + ["--preflight", "--store-id", "test-store"]) == 0
    assert cli.preflight.call_count == 2
    assert all(call.kwargs == {"require_empty": True} for call in cli.preflight.call_args_list)
    cli.preview.assert_awaited_once()
    cli.synchronize.assert_not_called()
    output = capsys.readouterr().out
    assert '"status": "ready_for_initial_sync"' in output
    assert '"writes": false' in output and '"loyverse_contacted": true' in output
    assert "PRIVATE" not in output


def test_initial_preflight_requires_token_and_store_before_connect(cli, monkeypatch):
    monkeypatch.setenv("LOYVERSE_STORE_ID", "test-store")
    assert sync_catalog.main(ARGS + ["--preflight", "--store-id", "test-store"]) == 1
    cli.factory.assert_not_called()
    cli.preview.assert_not_called()


def test_changed_database_after_download_cannot_report_ready(cli, monkeypatch, capsys):
    monkeypatch.setenv("LOYVERSE_ACCESS_TOKEN", "PRIVATE_TOKEN")
    monkeypatch.setenv("LOYVERSE_STORE_ID", "test-store")
    cli.preflight.side_effect = [cli.preflight.return_value, DatabaseTargetError("Las tablas cambiaron.")]
    assert sync_catalog.main(ARGS + ["--preflight", "--store-id", "test-store"]) == 1
    cli.preview.assert_awaited_once()
    cli.synchronize.assert_not_called()
    assert "ready_for_initial_sync" not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["valid", "another_store", "broken_page", "invalid_price", "empty"])
async def test_preview_uses_full_download_and_transform_without_persistence(commercial_snapshot, monkeypatch, mode):
    calls = []

    def respond(request):
        calls.append(request.url.path)
        assert request.method == "GET"
        if request.url.path.endswith("/stores"):
            store = commercial_snapshot.store if mode != "another_store" else {"id": "other", "name": "Otra"}
            return httpx.Response(200, json={"stores": [store]})
        assert request.url.params["show_deleted"] == "true"
        if request.url.path.endswith("/items"):
            if mode == "broken_page":
                return httpx.Response(200, json={"items": [], "cursor": "again"})
            items = list(commercial_snapshot.items) if mode != "empty" else []
            if mode == "invalid_price":
                from copy import deepcopy
                items = deepcopy(items)
                items[0]["variants"][0]["stores"][0]["price"] = None
            return httpx.Response(200, json={"items": items})
        return httpx.Response(200, json={"modifiers": list(commercial_snapshot.modifiers)})

    monkeypatch.setattr(sync_catalog, "LoyverseAPIClient", lambda **kwargs: LoyverseAPIClient(
        **kwargs, transport=httpx.MockTransport(respond),
    ))
    forbidden = MagicMock(side_effect=AssertionError("El preview intentó persistir"))
    monkeypatch.setattr(SyncService, "_write", forbidden)
    if mode == "valid":
        report = await sync_catalog.preview_catalog("test-store", SecretStr("fake-token"))
        assert report["planned_initial_inserts"] == {
            "Products": 3, "ProductVariants": 5, "ProductModifiers": 2,
            "ProductComplements": 5, "ProductModifierAssignments": 4,
        }
        assert report["commercially_available_variants"] == 5
        assert report["commercially_unavailable_variants"] == 0
    else:
        from app.services.loyverse_client import LoyverseAPIError
        with pytest.raises((LoyverseAPIError, CatalogValidationError, DatabaseTargetError)):
            await sync_catalog.preview_catalog("test-store", SecretStr("fake-token"))
    if mode == "another_store":
        assert calls == ["/v1.0/stores"]
    forbidden.assert_not_called()


def test_initial_preflight_rejects_nonempty_catalog(sync_test_database):
    target = MagicMock(spec=CloudDatabaseTarget)
    target.expected_database = sync_test_database.url.database
    target.expected_host = "isolated-postgresql"
    with sync_test_database.begin() as connection:
        connection.exec_driver_sql(
            'INSERT INTO public."Products" (loyverse_id, name, synced_at) '
            "VALUES ('preflight-fixture', 'Simulado', now())"
        )
    try:
        with pytest.raises(DatabaseTargetError, match="ocho tablas vacías"):
            sync_catalog.preflight(sync_test_database, target, require_empty=True)
        assert sync_catalog.preflight(sync_test_database, target)["table_counts"]["Products"] == 1
    finally:
        with sync_test_database.begin() as connection:
            connection.exec_driver_sql('DELETE FROM public."Products" WHERE loyverse_id = \'preflight-fixture\'')


@pytest.mark.parametrize("failure", range(24))
def test_operator_requires_every_individual_privilege(failure):
    connection = MagicMock()
    connection.execute.return_value.one.return_value = ("operator", "operator", "off", False)
    connection.engine.url.username = "operator"
    connection.connection.driver_connection.readonly = None
    # CONNECT/USAGE + nueve SELECT/resolución + diez DML + cuatro secuencias.
    connection.scalar.side_effect = [True] * failure + [False]
    with pytest.raises(DatabaseTargetError):
        sync_catalog._inspect_operator(connection)


@pytest.mark.parametrize("user,session_user,default,driver_readonly,recovery", [
    ("mitbol_render_reader", "mitbol_render_reader", "off", None, False),
    ("other", "operator", "off", None, False),
    ("operator", "operator", "on", None, False),
    ("operator", "operator", "off", True, False),
    ("operator", "operator", "off", None, True),
])
def test_operator_rejects_reader_role_switch_and_inherited_readonly(user, session_user, default, driver_readonly, recovery):
    connection = MagicMock()
    connection.execute.return_value.one.return_value = (user, session_user, default, recovery)
    connection.engine.url.username = "operator"
    connection.connection.driver_connection.readonly = driver_readonly
    with pytest.raises(DatabaseTargetError):
        sync_catalog._inspect_operator(connection)
    connection.scalar.assert_not_called()

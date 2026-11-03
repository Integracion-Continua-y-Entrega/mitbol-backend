"""Operación manual con credenciales ficticias; Loyverse siempre está simulado."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from alembic.runtime.migration import MigrationContext
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import event, select

from app.cli import sync_catalog
from app.db.connection import CloudDatabaseTarget, DatabaseTargetError
from app.models import Product
from app.services.catalog_errors import CatalogTargetError, CatalogValidationError
from app.services.loyverse_client import LoyverseAPIClient, LoyverseConfigurationError
from app.services.sync_service import SyncService, validate_catalog_revision


HOST = "ep-catalog.example.com"
NAME = "mitbol_cloud_dev"
URL = f"postgresql+psycopg2://operator:PRIVATE_PASSWORD@{HOST}/{NAME}?sslmode=verify-full&channel_binding=require"
ARGS = ["--expected-database", NAME, "--expected-host", HOST]


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setenv("APP_ENV", "cloud")
    monkeypatch.setenv("EXPECTED_DATABASE", NAME)
    monkeypatch.setenv("EXPECTED_DATABASE_HOST", HOST)
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.delenv("LOYVERSE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LOYVERSE_STORE_ID", raising=False)
    engine = MagicMock()
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(sync_catalog, "create_database_engine", factory)
    preflight = MagicMock(return_value={"status": "preflight_ok", "writes": False, "loyverse_contacted": False})
    monkeypatch.setattr(sync_catalog, "preflight", preflight)
    synchronize = AsyncMock(return_value={"status": "synchronized"})
    monkeypatch.setattr(sync_catalog, "synchronize", synchronize)
    return SimpleNamespace(engine=engine, factory=factory, preflight=preflight, synchronize=synchronize)


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

"""Web con rol limitado -> worker HTTP privado -> operador -> Loyverse simulado."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine, event, text
from alembic import command

from test_auth_persistence import auth_database, config_for
from test_auth_api import backend as owner_backend, password_hash, login, csrf_headers
from test_admin_writer import ROOT, denied, execute_script
from app.main import create_app
from app.services import sync_worker_runtime as runtime_module, sync_worker_client as client_module
from app.services.loyverse_client import LoyverseAPIClient
from app.services.sync_worker_runtime import SyncWorkerConfig, SyncWorkerRuntime
from app.sync_worker import create_worker
from app.models import catalog_metadata
from app.services.manual_sync_coordinator import SyncDestinationMismatchError


STORE = "a3a56a06-d40c-4c07-931c-41abcbaaba12"
WORKER_TOKEN = "synthetic-worker-token-" + "x" * 32


@pytest.fixture
def database(auth_database):
    with auth_database.begin() as db:
        command.upgrade(config_for(db), "20261111_0006")
    try:
        yield auth_database
    finally:
        with auth_database.begin() as db:
            for table in reversed(catalog_metadata.sorted_tables):
                db.execute(table.delete())
            command.downgrade(config_for(db), "20261106_0005")


@pytest.fixture
def backend(owner_backend, database, monkeypatch, commercial_snapshot):
    b = owner_backend
    # El nombre, directorio, operador y PG18 ya fueron validados por la fixture.
    source = (ROOT / "docs/database/prepare_admin_writer_local.sql").read_text(encoding="utf-8")
    reader_source = (ROOT / "docs/database/prepare_render_reader.sql").read_text(encoding="utf-8")
    execute_script(database, reader_source.replace("mitbol_cloud_dev", database.url.database).replace("neondb_owner", "mitbol_ci"))
    execute_script(database, source)
    password = None
    if database.url.password:
        password = "synthetic-writer-fixture"
        with database.begin() as db:
            db.exec_driver_sql("ALTER ROLE mitbol_admin_writer PASSWORD 'synthetic-writer-fixture'")
            db.exec_driver_sql("ALTER ROLE mitbol_render_reader PASSWORD 'synthetic-writer-fixture'")
    worker_config = SyncWorkerConfig(
        database_url=database.url.render_as_string(hide_password=False),
        expected_database=database.url.database, store_id=STORE,
        loyverse_token=SecretStr("synthetic-loyverse-token"), worker_token=SecretStr(WORKER_TOKEN),
    )
    runtime = SyncWorkerRuntime(worker_config)
    worker = create_worker(runtime=runtime)
    calls, observed = [], []
    state = SimpleNamespace(block=False, bad=False, entered=Event(), release=Event())
    snapshot = deepcopy(commercial_snapshot)
    snapshot = replace(snapshot, store={"id": STORE, "name": "Tienda simulada"})
    for item in snapshot.items:
        for variant in item.get("variants", []):
            for store in variant.get("stores", []):
                store["store_id"] = STORE
    for modifier in snapshot.modifiers:
        modifier["stores"] = [STORE]

    async def provider(request):
        assert request.method == "GET"
        calls.append(request.url.path)
        path = request.url.path.rsplit("/", 1)[-1]
        if path == "stores":
            return httpx.Response(200, json={"stores": [snapshot.store]})
        if path == "items":
            return httpx.Response(200, json={"items": list(snapshot.items)})
        if path == "modifiers":
            return httpx.Response(200, json={"modifiers": list(snapshot.modifiers)})
        assert path == "discounts"
        state.entered.set()
        if state.block:
            for _ in range(1500):
                if state.release.is_set():
                    break
                await asyncio.sleep(.01)
            else:
                raise RuntimeError("La prueba no liberó el proveedor")
        if state.bad:
            return httpx.Response(200, json={"discounts": [{"id": "invalid"}]})
        return httpx.Response(200, json={"discounts": [{
            "id": "497f6eca-6276-4993-bfeb-53cbbbba6f08", "name": "Descuento simulado",
            "type": "VARIABLE_AMOUNT", "discount_amount": None, "discount_percent": None,
            "stores": [], "restricted_access": False, "deleted_at": None,
            "created_at": "2026-11-01T12:00:00Z", "updated_at": "2026-11-12T12:00:00Z",
        }]})

    monkeypatch.setattr(runtime_module, "LoyverseAPIClient", lambda **kwargs: LoyverseAPIClient(
        **kwargs, transport=httpx.MockTransport(provider)))
    real_client = client_module.SyncWorkerClient
    monkeypatch.setattr(client_module, "SyncWorkerClient", lambda url, token, **kwargs: real_client(
        url, token, **kwargs, transport=httpx.ASGITransport(app=worker)))
    config = b.config.model_copy(update={
        "database_url": database.url.set(username="mitbol_render_reader", password=password).render_as_string(hide_password=False),
        "auth_database_url": database.url.set(username="mitbol_admin_writer", password=password).render_as_string(hide_password=False),
        "loyverse_store_id": STORE,
        "sync_worker_url": "http://127.0.0.1:8766", "sync_worker_token": SecretStr(WORKER_TOKEN),
    })
    config.validate_sync_worker()
    app = create_app(config)
    app.state.auth_service.clock = lambda: b.now.value

    def record(conn, cursor, statement, parameters, context, many):
        observed.append(statement)  # Sin registrar parámetros o secretos.
    event.listen(app.state.auth_session_factory.kw["bind"], "before_cursor_execute", record)
    try:
        with TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
            yield SimpleNamespace(**{**vars(b), "client": client, "app": app, "config": config},
                owner=database, worker_config=worker_config, runtime=runtime, state=state, calls=calls, observed=observed)
    finally:
        state.release.set()
        runtime.close()
        with database.begin() as db:
            db.exec_driver_sql("DROP OWNED BY mitbol_admin_writer")
            db.exec_driver_sql("DROP ROLE mitbol_admin_writer")
            db.exec_driver_sql("DROP OWNED BY mitbol_render_reader")
            db.exec_driver_sql("DROP ROLE mitbol_render_reader")


@pytest.mark.parametrize("target", ["catalog", "discounts"])
def test_admin_executes_through_worker_and_commits_with_operator_role(backend, target):
    b = backend
    headers = csrf_headers(login(b))
    response = b.client.post("/api/admin/sync/requests", json={"target": target}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["target"] == target and response.json()["status"] == "completed"
    with b.owner.connect() as db:
        table = '"Products"' if target == "catalog" else '"Discounts"'
        assert db.scalar(text("SELECT count(*) FROM public." + table)) > 0
    assert not any('INSERT INTO "Products"' in sql or 'INSERT INTO "Discounts"' in sql for sql in b.observed)
    if target == "catalog":
        menu = b.client.get("/api/menu")
        assert menu.status_code == 200 and menu.json()


@pytest.mark.parametrize("target", ["catalog", "discounts"])
@pytest.mark.parametrize("field,value", [
    ("database", "other_database"),
    ("store_id", "497f6eca-6276-4993-bfeb-53cbbbba6f08"),
])
def test_misrouted_manual_request_cannot_contact_provider_or_change_catalog(backend, target, field, value):
    b = backend
    destination = b.config.sync_destination.model_copy(update={field: value})
    b.app.state.manual_sync_coordinator = client_module.SyncWorkerClient(
        b.config.sync_worker_url, b.config.sync_worker_token, destination=destination).coordinator()
    with b.owner.connect() as db:
        before = {table.name: db.execute(table.select()).all() for table in catalog_metadata.sorted_tables}
        before_discounts = db.exec_driver_sql('SELECT * FROM public."Discounts"').all()
    response = b.client.post("/api/admin/sync/requests", json={"target": target}, headers=csrf_headers(login(b)))
    assert response.status_code == 503 and b.calls == []
    assert "destino" in response.json()["detail"]
    with b.owner.connect() as db:
        assert {table.name: db.execute(table.select()).all() for table in catalog_metadata.sorted_tables} == before
        assert db.exec_driver_sql('SELECT * FROM public."Discounts"').all() == before_discounts


@pytest.mark.parametrize("target", ["catalog", "discounts"])
def test_actual_connection_must_match_worker_configuration_before_provider(backend, target):
    b = backend
    # Configuración válida por separado, pero engine de una base distinta.
    config = replace(b.worker_config, database_url="postgresql://operator@127.0.0.1/mitbol_dev",
                     expected_database="mitbol_dev")
    runtime = SyncWorkerRuntime(config, engine=b.owner)
    with pytest.raises(SyncDestinationMismatchError):
        asyncio.run(runtime.execute(target))
    assert b.calls == []


def test_browser_cannot_override_server_destination(backend):
    b = backend
    response = b.client.post("/api/admin/sync/requests", json={
        "target": "discounts", "destination": b.config.sync_destination.model_dump(),
    }, headers=csrf_headers(login(b)))
    assert response.status_code == 422 and b.calls == []


def test_staff_is_denied_before_body_validation_or_provider(backend):
    b = backend
    headers = csrf_headers(login(b, "staff"))
    response = b.client.post("/api/admin/sync/requests", json={"target": "all"}, headers=headers)
    assert response.status_code == 403 and b.calls == []


@pytest.mark.parametrize("fault", ["csrf", "origin"])
def test_manual_request_requires_csrf_and_origin(backend, fault):
    b = backend
    headers = csrf_headers(login(b))
    del headers["X-CSRF-Token" if fault == "csrf" else "Origin"]
    assert b.client.post("/api/admin/sync/requests", json={"target": "discounts"}, headers=headers).status_code == 403
    assert b.calls == []


def test_missing_worker_configuration_never_falls_back_to_web_database(backend):
    b = backend
    b.app.state.manual_sync_coordinator = None
    response = b.client.post("/api/admin/sync/requests", json={"target": "discounts"}, headers=csrf_headers(login(b)))
    assert response.status_code == 503 and b.calls == []


def test_invalid_body_does_not_echo_credentials(backend):
    b = backend
    response = b.client.post("/api/admin/sync/requests", json={"target": "all", "token": "private-password"}, headers=csrf_headers(login(b)))
    assert response.status_code == 422 and "private-password" not in response.text and b.calls == []


def test_provider_failure_preserves_previously_committed_data(backend):
    b = backend
    headers = csrf_headers(login(b))
    assert b.client.post("/api/admin/sync/requests", json={"target": "discounts"}, headers=headers).status_code == 200
    with b.owner.connect() as db:
        before = db.exec_driver_sql('SELECT * FROM public."Discounts"').all()
    b.state.bad = True
    assert b.client.post("/api/admin/sync/requests", json={"target": "discounts"}, headers=headers).status_code == 502
    with b.owner.connect() as db:
        assert db.exec_driver_sql('SELECT * FROM public."Discounts"').all() == before


def test_status_polling_duplicate_manual_and_periodic_skip_during_execution(backend, monkeypatch, capsys):
    from app.cli import run_sync
    b = backend
    headers = csrf_headers(login(b))
    b.state.block = True
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(b.client.post, "/api/admin/sync/requests", json={"target": "discounts"}, headers=headers)
        try:
            assert b.state.entered.wait(10)
            status = b.client.get("/api/admin/sync/status")
            assert status.status_code == 200 and status.json()["execution_active"] is True
            duplicate = b.client.post("/api/admin/sync/requests", json={"target": "discounts"}, headers=headers)
            assert duplicate.status_code == 409
            monkeypatch.setattr(run_sync.SyncWorkerConfig, "from_environment", lambda: b.worker_config)
            assert run_sync.main(["--target", "discounts", "--execute", "--skip-if-busy"]) == 0
            assert "skipped_busy" in capsys.readouterr().out
            assert len(b.calls) == 1
        finally:
            b.state.release.set()
        assert future.result(timeout=10).status_code == 200
    assert b.client.get("/api/admin/sync/status").json()["execution_active"] is False

"""HU-010 con sesiones, migración y PostgreSQL reales desechables; HTTP simulado."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from io import StringIO
import json
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, delete, event, func, inspect, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from test_auth_persistence import auth_database, config_for
from test_auth_api import backend as auth_backend, password_hash, login, csrf_headers
from test_recurring_catalog_sync import FakeCatalogAPI
from test_discount_migration import discount_snapshot
from app.cli import sync_catalog_jobs as cli
from app.core.config import Settings
from app.main import create_app
from app.models import AuthBase, Base, Product, ProductImage, ProductTag, ProductVariant, Tag, catalog_metadata
from app.models.sync_run import SyncBase, SyncRun
from app.services.catalog_errors import CatalogTargetError, CatalogValidationError
from app.services.discount_sync_service import DiscountSyncService
from app.services.loyverse_webhook_signature import verify_signature
from app.services.recurring_catalog_sync import RecurringCatalogSync
from app.services import sync_jobs as jobs
from app.services.sync_service import CATALOG_SYNC_LOCK_KEY, SyncService


@pytest.fixture(scope="module")
def management_database(auth_database):
    with auth_database.begin() as connection:
        command.upgrade(config_for(connection), jobs.SYNC_JOBS_REVISION)
    return auth_database


@pytest.fixture
def backend(management_database, auth_backend):
    b = auth_backend
    config = Settings(**{**b.config.model_dump(), "_env_file": None, "auth_sync_enabled": True,
        "database_url": management_database.url.render_as_string(hide_password=False),
        "expected_database": management_database.url.database, "expected_database_host": management_database.url.host,
        "loyverse_store_id": "test-store"})
    application = create_app(config)
    application.state.auth_service.clock = lambda: b.now.value
    assert not application.dependency_overrides
    with TestClient(application, base_url="http://localhost:8000", client=("127.0.0.1", 54321)) as client:
        yield SimpleNamespace(**{**vars(b), "client": client, "app": application, "config": config,
                                 "engine": management_database})
    with b.sessions.begin() as db:
        db.execute(delete(SyncRun))
        for table in reversed(catalog_metadata.sorted_tables):
            db.execute(delete(table))


def operator(b):
    runner = RecurringCatalogSync(b.sessions, expected_database=b.engine.url.database,
        expected_host=b.engine.url.host, store_id="test-store", auth_catalog=True)
    approval = {"operation": "catalog_recurrent_v1", "approved": True,
        "host": b.engine.url.host, "database": b.engine.url.database,
        "user": b.engine.url.username, "store_id": "test-store", "revision": jobs.SYNC_JOBS_REVISION}
    return runner, approval


def process(b, snapshot, *, api=None, scheduled=False, worker=None, approval=None):
    runner, approved = operator(b)
    api = api or FakeCatalogAPI(snapshot)

    async def invoke():
        async with api.client() as client:
            return await (worker or jobs.SyncJobWorker(runner)).run_once(client,
                approval=approval or approved, scheduled=scheduled)

    return asyncio.run(invoke())


def request_sync(b):
    auth = login(b)
    assert auth.status_code == 200
    response = b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth))
    assert response.status_code == 202, response.text
    return response, auth


def catalog_state(b):
    with b.sessions() as db:
        return {name: [dict(row) for row in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()]
                for name, table in catalog_metadata.tables.items()}


def test_migration_is_additive_reversible_and_offline(management_database, monkeypatch):
    output = StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(Engine, "connect", lambda *a, **k: pytest.fail("SQL offline no debe conectar"))
        command.upgrade(config_for(output=output), "20261111_0006:20261114_0007", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE") == 1 and 'CREATE TABLE public."SyncRuns"' in sql
    assert all(word not in sql for word in ("DROP TABLE", "ALTER TABLE", "GRANT ", "CREATE ROLE"))
    with management_database.begin() as connection:
        assert not compare_metadata(MigrationContext.configure(connection), [
            Base.metadata, AuthBase.metadata, SyncBase.metadata])
        before = set(inspect(connection).get_table_names()) - {"SyncRuns"}
        command.downgrade(config_for(connection), "20261111_0006")
        assert set(inspect(connection).get_table_names()) == before
        command.upgrade(config_for(connection), jobs.SYNC_JOBS_REVISION)
        assert set(inspect(connection).get_table_names()) == before | {"SyncRuns"}


def test_hu009_accepts_the_additive_hu010_revision(sync_test_database):
    engine = sync_test_database
    with engine.connect() as connection, connection.begin():
        command.upgrade(config_for(connection), jobs.SYNC_JOBS_REVISION)
        service = DiscountSyncService(sessionmaker(bind=connection, join_transaction_mode="create_savepoint"),
            expected_database=engine.url.database, store_id="a3a56a06-d40c-4c07-931c-41abcbaaba12")
        assert service.persist_snapshot(discount_snapshot()) == 1
        connection.rollback()


def test_status_empty_manual_accepted_persisted_and_history(backend, commercial_snapshot):
    b = backend
    auth = login(b)
    assert b.client.get("/api/admin/sync/status").json()["last_run"] is None
    response = b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth))
    assert response.status_code == 202 and response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["created"] and payload["run"]["status"] == "queued"
    assert catalog_state(b)["Products"] == []  # HTTP no ejecuta el trabajo.
    with b.sessions() as db:
        row = db.get(SyncRun, UUID(payload["run"]["run_id"]))
        assert row.requested_by == b.ids["admin"][0] and row.source == "manual"
    result = process(b, commercial_snapshot)
    assert result["run"]["status"] == "succeeded"
    menu = b.client.get("/api/menu")
    assert menu.status_code == 200 and len(menu.json()["products"]) == 3
    queried = b.client.get(response.headers["location"])
    assert queried.status_code == 200
    assert queried.json()["result"]["changes"]["products"]["new"] == 3
    assert all(field not in queried.text for field in ("attempt_token", "DATABASE_URL", "target", "access_token"))
    status = b.client.get("/api/admin/sync/status").json()
    assert status["last_successful_run"]["run_id"] == payload["run"]["run_id"]
    assert status["last_catalog_synced_at"] and not status["is_running"] and status["active_run"] is None
    assert status["webhooks_enabled"] is False
    assert len(b.client.get("/api/admin/sync/runs").json()["runs"]) == 1


@pytest.mark.parametrize("scenario,expected", [("anonymous", 401), ("staff", 403), ("csrf", 403), ("origin", 403)])
def test_manual_requires_session_permission_and_csrf(backend, scenario, expected):
    b = backend
    headers = {"Origin": "http://localhost:5173"}
    if scenario != "anonymous":
        auth = login(b, "staff" if scenario == "staff" else "admin")
        headers = csrf_headers(auth)
    if scenario == "csrf":
        headers.pop("X-CSRF-Token")
    if scenario == "origin":
        headers["Origin"] = "https://attacker.example"
    response = b.client.post("/api/admin/sync/runs", json={}, headers=headers)
    assert response.status_code == expected
    with b.sessions() as db:
        assert db.scalar(select(func.count()).select_from(SyncRun)) == 0


def test_staff_cannot_read_history_or_status(backend):
    b = backend
    login(b, "staff")
    for path in ("/status", "/runs", "/runs/" + str(uuid4())):
        assert b.client.get("/api/admin/sync" + path).status_code == 403


def test_accepted_job_survives_application_restart_and_coalesces(backend, commercial_snapshot):
    b = backend
    accepted, auth = request_sync(b)
    duplicate = b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth))
    assert duplicate.status_code == 202 and not duplicate.json()["created"]
    assert duplicate.json()["run"]["run_id"] == accepted.json()["run"]["run_id"]
    with TestClient(create_app(b.config), base_url="http://localhost:8000", client=("127.0.0.1", 11111)) as restarted:
        restarted.cookies.update(b.client.cookies)
        assert restarted.get(accepted.headers["location"]).json()["status"] == "queued"
    process(b, commercial_snapshot)
    assert b.client.get(accepted.headers["location"]).json()["status"] == "succeeded"


def test_real_concurrent_worker_busy_and_running_status(backend, commercial_snapshot):
    b = backend
    accepted, auth = request_sync(b)
    started, release = Event(), Event()

    def pause(request, key):
        if key == "items" and not request.url.params.get("cursor"):
            started.set()
            assert release.wait(10)
        return None

    api = FakeCatalogAPI(commercial_snapshot, pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(process, b, commercial_snapshot, api=api)
        try:
            assert started.wait(10)
            status = b.client.get("/api/admin/sync/status").json()
            assert status["is_running"] and status["catalog_locked"] and status["worker_connected"]
            assert not status["recovery_pending"]
            other = FakeCatalogAPI(commercial_snapshot)
            assert process(b, commercial_snapshot, api=other) == {"outcome": "busy"}
            assert other.requests == []
            assert b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth)).json()["created"] is False
        finally:
            release.set()
        assert first.result(timeout=10)["run"]["status"] == "succeeded"


def test_recover_interrupted_running_job(backend, commercial_snapshot):
    b = backend
    accepted, _ = request_sync(b)
    runner, _ = operator(b)
    worker = jobs.SyncJobWorker(runner)
    with jobs.worker_lock(b.engine) as acquired:
        assert acquired
        identifier, token = worker.claim()
    status = b.client.get("/api/admin/sync/status").json()
    assert status["recovery_pending"] and not status["is_running"]
    assert process(b, commercial_snapshot)["run"]["attempts"] == 2
    assert b.client.get(accepted.headers["location"]).json()["status"] == "succeeded"


def test_recovery_has_a_bounded_attempt_limit(backend, commercial_snapshot):
    b = backend
    accepted, _ = request_sync(b)
    with b.sessions.begin() as db:
        db.execute(update(SyncRun).values(status="running", attempts=3, attempt_token=uuid4()))
    api = FakeCatalogAPI(commercial_snapshot)
    result = process(b, commercial_snapshot, api=api)
    assert result["run"]["status"] == "failed" and result["run"]["error"]["code"] == "interrupted"
    assert not api.requests
    assert cli.exit_code(result) == 6


@pytest.mark.parametrize("attempts", [0, 2])
def test_provider_failure_is_persisted_sanitized_and_retryable(backend, commercial_snapshot, attempts):
    b = backend
    accepted, _ = request_sync(b)
    with b.sessions.begin() as db:
        db.execute(update(SyncRun).values(attempts=attempts))
    before = catalog_state(b)
    api = FakeCatalogAPI(commercial_snapshot, lambda request, key: httpx.Response(503, json={"secret": "PRIVATE_TOKEN"}))
    failed = process(b, commercial_snapshot, api=api)
    assert failed["run"]["status"] == ("retry_wait" if attempts == 0 else "failed")
    response = b.client.get(accepted.headers["location"])
    assert response.json()["error"]["code"] == "provider_temporary" and "PRIVATE" not in response.text
    assert catalog_state(b) == before
    if attempts == 0:
        assert process(b, commercial_snapshot)["outcome"] == "idle"
        with b.sessions.begin() as db:
            db.execute(update(SyncRun).values(available_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        assert process(b, commercial_snapshot)["run"]["status"] == "succeeded"
    else:
        next_run, _ = request_sync(b)
        assert next_run.json()["run"]["run_id"] != accepted.json()["run"]["run_id"]
        assert process(b, commercial_snapshot)["run"]["status"] == "succeeded"


def test_catalog_and_success_history_commit_atomically(backend, commercial_snapshot, monkeypatch):
    b = backend
    request_sync(b)
    before = catalog_state(b)
    original = jobs.SyncJobWorker.success

    def fail_after_result(db, identifier, token, report):
        original(db, identifier, token, report)
        raise SQLAlchemyError("PRIVATE_CONNECTION_AND_TOKEN")

    with monkeypatch.context() as patch:
        patch.setattr(jobs.SyncJobWorker, "success", staticmethod(fail_after_result))
        result = process(b, commercial_snapshot)
    assert result["run"]["status"] == "retry_wait" and result["run"]["result"] is None
    assert catalog_state(b) == before
    assert result["run"]["error"]["code"] == "database"


def test_stale_attempt_cannot_commit_catalog(backend, commercial_snapshot, monkeypatch):
    b = backend
    request_sync(b)
    before = catalog_state(b)
    original = jobs.SyncJobWorker.success

    def stale(db, identifier, token, report):
        original(db, identifier, uuid4(), report)

    monkeypatch.setattr(jobs.SyncJobWorker, "success", staticmethod(stale))
    assert process(b, commercial_snapshot)["outcome"] == "superseded"
    assert catalog_state(b) == before


def test_managed_repeat_preserves_local_enrichment(backend, commercial_snapshot):
    b = backend
    process(b, commercial_snapshot, scheduled=True)
    with b.sessions.begin() as db:
        product = db.scalar(select(Product).where(Product.loyverse_id == "burger"))
        variant = db.scalar(select(ProductVariant).where(ProductVariant.loyverse_id == "single"))
        product.description, product.is_available, product.is_featured, product.is_new = "Local", False, True, False
        variant.is_available = False
        tag = Tag(name="local")
        db.add(tag)
        db.flush()
        db.add_all([ProductTag(product_variant_id=variant.product_variant_id, tag_id=tag.tag_id),
                    ProductImage(product_variant_id=variant.product_variant_id, image_url="/media/products/local-r2.webp", display_order=3)])
    before = catalog_state(b)
    changed = deepcopy(commercial_snapshot)
    changed.items[0]["variants"][0]["stores"][0]["price"] = 101
    updated = process(b, changed, scheduled=True)
    assert updated["run"]["result"]["changes"]["variants"]["updated"] == 1
    after = catalog_state(b)
    for name in ("ProductImages", "Tags", "ProductTags"):
        assert after[name] == before[name]
    product = next(row for row in after["Products"] if row["loyverse_id"] == "burger")
    assert (product["description"], product["is_available"], product["is_featured"], product["is_new"]) == ("Local", False, True, False)
    assert not next(row for row in after["ProductVariants"] if row["loyverse_id"] == "single")["is_available"]
    repeated = process(b, changed, scheduled=True)
    assert repeated["run"]["result"]["changes"]["products"]["unchanged"] == 3


def test_unauthorized_worker_scope_cannot_touch_queue(backend, commercial_snapshot):
    b = backend
    accepted, _ = request_sync(b)
    _, approval = operator(b)
    api = FakeCatalogAPI(commercial_snapshot)
    with pytest.raises(CatalogTargetError):
        process(b, commercial_snapshot, api=api, approval={**approval, "user": "other"})
    assert not api.requests
    assert b.client.get(accepted.headers["location"]).json()["attempts"] == 0


def test_existing_hu008_lock_defers_managed_job_without_consuming_attempts(backend, commercial_snapshot):
    b = backend
    accepted, _ = request_sync(b)
    api = FakeCatalogAPI(commercial_snapshot)
    from app.services.catalog_errors import CatalogSyncBusyError
    with b.engine.begin() as connection:
        assert connection.scalar(select(func.pg_try_advisory_xact_lock(CATALOG_SYNC_LOCK_KEY)))
        assert b.client.get("/api/admin/sync/status").json()["catalog_locked"]
        with pytest.raises(CatalogSyncBusyError):
            process(b, commercial_snapshot, api=api)
    assert not api.requests
    assert b.client.get(accepted.headers["location"]).json()["attempts"] == 0
    assert process(b, commercial_snapshot)["run"]["status"] == "succeeded"


def test_queue_commit_failure_never_returns_accepted_or_leaves_work(backend):
    b = backend
    auth = login(b)
    engine = b.app.state.auth_session_factory.kw["bind"]
    pending = set()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO public."SyncRuns"'):
            pending.add(connection)

    def fail(connection):
        if connection in pending:
            raise SQLAlchemyError("PRIVATE_CONNECTION_AND_TOKEN")

    event.listen(engine, "before_cursor_execute", observe)
    event.listen(engine, "commit", fail)
    try:
        response = b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth))
        assert response.status_code == 503 and "PRIVATE" not in response.text
    finally:
        event.remove(engine, "commit", fail)
        event.remove(engine, "before_cursor_execute", observe)
    with b.sessions() as db:
        assert db.scalar(select(func.count()).select_from(SyncRun)) == 0


def test_enqueue_contention_is_immediate_and_wrong_store_is_rejected(backend):
    b = backend
    auth = login(b)
    with b.engine.begin() as connection:
        assert connection.scalar(select(func.pg_try_advisory_xact_lock(jobs.QUEUE_LOCK_KEY)))
        assert b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth)).status_code == 409
    accepted = b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth))
    assert accepted.status_code == 202
    with b.sessions.begin() as db:
        db.execute(update(SyncRun).values(store_id="other-store"))
    assert b.client.post("/api/admin/sync/runs", json={}, headers=csrf_headers(auth)).status_code == 409


def test_real_queue_permissions_do_not_implicitly_allow_enqueue(backend):
    b = backend
    role = "hu010_readonly_" + uuid4().hex[:12]
    with b.engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE ROLE "{role}" LOGIN')
        connection.exec_driver_sql(f'GRANT SELECT ON public."SyncRuns", public.alembic_version TO "{role}"')
    engine = create_engine(b.engine.url.set(username=role), hide_parameters=True)
    try:
        with sessionmaker(bind=engine).begin() as db:
            jobs.inspect_queue(db, expected_database=engine.url.database, expected_host=engine.url.host)
            with pytest.raises(CatalogTargetError):
                jobs.inspect_queue(db, expected_database=engine.url.database, expected_host=engine.url.host,
                                   permission="enqueue")
    finally:
        engine.dispose()
        with b.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
            connection.exec_driver_sql(f'DROP ROLE "{role}"')


def test_legacy_profile_still_rejects_auth_database_and_missing_queue_schema(backend):
    b = backend
    with pytest.raises(CatalogTargetError):
        SyncService(b.sessions, expected_database=b.engine.url.database, store_id="test-store")
    login(b)
    with b.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE public.alembic_version SET version_num='20261111_0006'")
    try:
        assert b.client.get("/api/admin/sync/status").status_code == 503
    finally:
        with b.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE public.alembic_version SET version_num='20261114_0007'")


def test_history_pagination_missing_run_and_validation_sanitized(backend, commercial_snapshot):
    b = backend
    login(b)
    process(b, commercial_snapshot, scheduled=True)
    process(b, commercial_snapshot, scheduled=True)
    page = b.client.get("/api/admin/sync/runs?limit=1").json()
    assert len(page["runs"]) == 1 and page["next_offset"] == 1
    assert b.client.get("/api/admin/sync/runs?limit=1&offset=1").json()["next_offset"] is None
    assert b.client.get("/api/admin/sync/runs/" + str(uuid4())).status_code == 404
    invalid = b.client.get("/api/admin/sync/runs/PRIVATE_TOKEN")
    assert invalid.status_code == 422 and "PRIVATE_TOKEN" not in invalid.text


def test_cli_scheduled_execution_and_persistent_result(backend, commercial_snapshot, monkeypatch, capsys):
    b = backend
    _, approval = operator(b)
    for key, value in {"APP_ENV": "test", "ADMIN_ENABLED": "false", "EXPECTED_DATABASE": b.engine.url.database,
        "EXPECTED_DATABASE_HOST": b.engine.url.host, "DATABASE_URL": b.engine.url.render_as_string(hide_password=False),
        "LOYVERSE_STORE_ID": "test-store", "LOYVERSE_ACCESS_TOKEN": "synthetic",
        "MITBOL_RECURRENT_SYNC_APPROVAL": json.dumps(approval)}.items():
        monkeypatch.setenv(key, value)
    api = FakeCatalogAPI(commercial_snapshot)
    monkeypatch.setattr(cli, "LoyverseAPIClient", lambda **kwargs: api.client())
    args = ["--expected-database", b.engine.url.database, "--expected-host", b.engine.url.host, "--store-id", "test-store"]
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "validation"
    assert not api.requests
    assert cli.main([*args, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["writes"] is False and preview["approval_template"]["approved"] is False
    assert catalog_state(b)["Products"] == []
    with b.sessions() as db:
        assert db.scalar(select(func.count()).select_from(SyncRun)) == 0
    assert cli.main([*args, "--execute", "--enqueue-scheduled"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run"]["source"] == "scheduled" and report["run"]["status"] == "succeeded"


def test_webhooks_not_registered_even_when_signature_header_supplied(backend):
    b = backend
    assert not any("webhook" in getattr(route, "path", "") for route in b.app.routes)
    for headers in ({}, {"X-Loyverse-Signature": "a" * 40}):
        response = b.client.post("/api/webhooks/loyverse", headers=headers,
                                 json={"type": "items.update", "merchant_id": "synthetic"})
        assert response.status_code == 404
    with b.sessions.begin() as db:
        with pytest.raises(CatalogValidationError, match="deshabilitada"):
            jobs.enqueue(db, store_id="test-store", source="webhook")
        assert db.scalar(select(func.count()).select_from(SyncRun)) == 0


def test_signature_preparation_uses_original_bytes_and_fails_closed():
    secret, body = "synthetic-oauth-app-secret", b'{"type": "items.update", "merchant_id": "synthetic"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    assert verify_signature(body, signature, secret)
    assert not verify_signature(body + b" ", signature, secret)
    assert not verify_signature(body, signature, "wrong")
    assert not verify_signature(body, signature, None)
    for invalid in (None, "", "sha1=" + signature, signature.upper(), "x" * 40):
        assert not verify_signature(body, invalid, secret)


@pytest.mark.parametrize("changes", [
    {"auth_enabled": False}, {"auth_catalog_enabled": False}, {"expected_database": None},
    {"expected_database_host": "remote.example"}, {"loyverse_store_id": None},
])
def test_feature_requires_valid_explicit_auth_configuration(backend, changes):
    with pytest.raises(ValidationError):
        Settings(**{**backend.config.model_dump(), **changes, "_env_file": None})


def test_feature_disabled_by_default_and_incomplete_cloud_is_closed(auth_backend):
    b = auth_backend
    assert not b.config.auth_sync_enabled
    login(b)
    assert b.client.get("/api/admin/sync/status").status_code == 404
    # Comprobación puntual del guard cloud sin fabricar una topología autorizada.
    copied = b.config.model_copy(update={"auth_sync_enabled": True, "app_env": "cloud"})
    with pytest.raises(ValueError):
        copied.validate_sync_configuration()

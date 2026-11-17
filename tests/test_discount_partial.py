"""Política estricta/parcial con commits reales sólo en PostgreSQL temporal."""

import asyncio
from dataclasses import replace
import json

from alembic import command
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker

from app.cli import sync_discounts as cli
from app.services import discount_sync_service as coordinator
from app.services.discount_probe import DiscountSnapshot
from app.services.discount_transform import DiscountValidationError, parse_discount
from test_discount_lifecycle import database
from test_discount_migration import staged_config
from test_discount_resolution import DiscountAPI, conflicts, OTHER_ID
from test_discount_transform import source_discount, STORE_ID, DISCOUNT_ID


@pytest.fixture
def private_directory(tmp_path):
    directory = tmp_path / "quarantine"
    directory.mkdir(mode=0o700)
    return directory


def service(engine, directory=None, *, partial=False):
    return coordinator.DiscountSyncService(sessionmaker(bind=engine),
        expected_database=engine.url.database, store_id=STORE_ID,
        allow_partial=partial, quarantine_dir=directory)


def run(runner, api):
    async def invoke():
        async with api.client() as client:
            return await runner.synchronize(client)
    return asyncio.run(invoke())


def state(engine):
    with engine.connect() as db:
        return (db.exec_driver_sql('SELECT * FROM public."Discounts" ORDER BY discount_id').all(),
                db.exec_driver_sql('SELECT last_value,is_called FROM public."Discounts_discount_id_seq"').one())


def unknown():
    return source_discount(id=OTHER_ID, type="TEYA_DISCOUNT", discount_percent=None)


def reports(directory):
    return [json.loads(path.read_text()) for path in sorted(directory.glob("discounts-*.json"))]


@pytest.mark.parametrize("revision", ["20261111_0006", "20261114_0007", "20261115_0008"])
def test_authorized_partial_preserves_originals_and_is_repeatable_on_supported_schemas(
        database, staged_config, private_directory, revision):
    with database.begin() as db:
        staged_config.attributes['connection'] = db
        command.upgrade(staged_config, revision)
    current, previous = conflicts()
    runner = service(database, private_directory, partial=True)
    points = source_discount(id=STORE_ID, type="DISCOUNT_BY_POINTS", discount_percent=None)
    api = DiscountAPI([current, previous, unknown(), points], current)
    assert run(runner, api) == 2
    before = state(database)[0]
    assert len(before) == 2 and before[0]._mapping['value'] == 10
    assert before[1]._mapping['discount_type'] == 'DISCOUNT_BY_POINTS' and before[1]._mapping['value'] is None
    assert run(runner, api) == 2
    after = state(database)[0]
    assert len(after) == 2
    for initial, repeated in zip(before, after):
        assert repeated._mapping['discount_id'] == initial._mapping['discount_id']
        assert repeated._mapping['created_at'] == initial._mapping['created_at']
    assert runner.last_report['partial'] and runner.last_report['isolated_ids'] == 1
    stored = reports(private_directory)
    assert len(stored) == 2
    assert stored[0]['context']['revision'] == revision
    assert stored[0]['quarantined'][0]['originals'] == [unknown()]
    assert stored[0]['resolutions'][0]['discarded'] == [previous]


def test_strict_unknown_retains_private_evidence_and_changes_no_rows_or_sequence(database, private_directory):
    runner = service(database, private_directory)
    before = state(database)
    with pytest.raises(DiscountValidationError, match="estricto"):
        run(runner, DiscountAPI([source_discount(discount_percent=10), unknown()]))
    assert state(database) == before and runner.last_report == {}
    assert reports(private_directory)[0]['decision'] == 'DISCOUNT_STRICT_REJECTED'


def test_partial_and_resolved_conflicts_require_explicit_report_destination(database):
    with pytest.raises(coordinator.DiscountSyncError, match="destino"):
        service(database, partial=True)
    runner = service(database)
    before = state(database)
    current, previous = conflicts()
    with pytest.raises(coordinator.DiscountSyncError, match="informe privado"):
        run(runner, DiscountAPI([current, previous], current))
    assert state(database) == before


@pytest.mark.parametrize("status", [200, 404])
def test_unresolved_conflict_is_omitted_without_creating_a_tombstone(database, private_directory, status):
    current, previous = conflicts()
    valid = source_discount(id=OTHER_ID, discount_percent=5)
    api = DiscountAPI([current, previous, valid], {**current, 'name':'Not observed'}, status=status)
    runner = service(database, private_directory, partial=True)
    assert run(runner, api) == 1
    rows = state(database)[0]
    assert len(rows) == 1 and rows[0]._mapping['loyverse_id'] == OTHER_ID
    assert reports(private_directory)[0]['quarantined'][0]['loyverse_id'] == DISCOUNT_ID


@pytest.mark.parametrize("uppercase", [False, True])
def test_omitted_id_already_local_aborts_before_any_commercial_dml(database, private_directory, uppercase):
    existing = source_discount(id=OTHER_ID, discount_percent=15)
    service(database).persist_snapshot(DiscountSnapshot((parse_discount(existing),), True))
    if uppercase:
        with database.begin() as db:
            db.exec_driver_sql('UPDATE public."Discounts" SET loyverse_id=upper(loyverse_id)')
    before = state(database)
    runner = service(database, private_directory, partial=True)
    with pytest.raises(DiscountValidationError, match="ya existe"):
        run(runner, DiscountAPI([source_discount(discount_percent=10), unknown()]))
    assert state(database) == before
    assert reports(private_directory)[0]['decision'] == 'DISCOUNT_OMITTED_ID_EXISTS'


def test_quarantine_write_failure_blocks_all_dml_and_sequence_usage(database, private_directory, monkeypatch):
    def failure(*args, **kwargs):
        raise coordinator.DiscountQuarantineError('No se pudo guardar el informe privado.')
    monkeypatch.setattr(coordinator, 'write_quarantine_report', failure)
    before = state(database)
    with pytest.raises(coordinator.DiscountSyncError, match="informe privado"):
        run(service(database, private_directory, partial=True),
            DiscountAPI([source_discount(discount_percent=10), unknown()]))
    assert state(database) == before


def test_partial_snapshot_cannot_bypass_verified_store_or_full_download(database, private_directory):
    runner = service(database, private_directory, partial=True)
    snapshot = DiscountSnapshot((parse_discount(source_discount()),), True)
    before = state(database)
    for changed in [snapshot, replace(snapshot, verified_store_id=STORE_ID, includes_deleted=False)]:
        with pytest.raises(coordinator.DiscountSyncError, match="tienda verificada"):
            runner.persist_snapshot(changed)
    assert state(database) == before and not reports(private_directory)


def test_sql_error_rolls_back_valid_rows_and_report_does_not_claim_commit(database, private_directory):
    runner = service(database, private_directory, partial=True)
    api = DiscountAPI([source_discount(discount_percent=10), source_discount(id=STORE_ID, discount_percent=5), unknown()])
    before = state(database)[0]
    writes = 0
    def reject_second(connection,cursor,statement,parameters,context,executemany):
        nonlocal writes
        if statement.startswith('INSERT INTO public."Discounts"'):
            writes += 1
            if writes == 2:
                connection.exec_driver_sql('SELECT 1/0')
    event.listen(database, 'before_cursor_execute', reject_second)
    try:
        with pytest.raises(coordinator.DiscountSyncError, match="persistencia"):
            run(runner, api)
    finally:
        event.remove(database, 'before_cursor_execute', reject_second)
    assert writes == 2 and state(database)[0] == before and runner.last_report == {}
    assert reports(private_directory)[0]['commercial_commit'] == 'not_attested_prepared_before_dml'


def test_cli_requires_partial_authorization_and_logs_only_summary(database, private_directory, monkeypatch, capsys):
    api = DiscountAPI([source_discount(discount_percent=10), unknown()])
    for key,value in dict(APP_ENV='test', DATABASE_URL=database.url.render_as_string(hide_password=False),
            LOYVERSE_STORE_ID=STORE_ID, LOYVERSE_ACCESS_TOKEN='synthetic-private-token').items():
        monkeypatch.setenv(key,value)
    monkeypatch.setattr(cli, 'LoyverseAPIClient', lambda **kwargs: api.client())
    args = ['--expected-database', database.url.database, '--store-id', STORE_ID]
    assert cli.main([*args,'--allow-partial']) == 1
    assert not api.requests
    capsys.readouterr()
    assert cli.main([*args,'--execute','--allow-partial','--quarantine-dir',str(private_directory)]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result['status'] == 'synchronized_with_quarantine' and result['processed'] == 1
    assert result['isolated_ids'] == 1 and result['quarantine_report_written']
    for private in ['synthetic-private-token', 'TEYA_DISCOUNT', OTHER_ID, str(private_directory)]:
        assert private not in output

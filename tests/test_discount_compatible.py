"""Política compatible explícita; PostgreSQL desechable y Loyverse simulado."""

from dataclasses import replace
import hashlib
import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.cli import sync_discounts as cli
from app.services.discount_probe import DiscountSnapshot
from app.services.discount_sync_service import DiscountSyncService, DiscountSyncError, _safe_incidents
from app.services.discount_transform import parse_discount, DiscountValidationError
from app.services.loyverse_client import LoyverseAPIError, LoyverseResponseError
from test_discount_lifecycle import database
from test_discount_migration import staged_config
from test_discount_partial import run, state, unknown, service as strict_service
from test_discount_resolution import DiscountAPI, conflicts, OTHER_ID
from test_discount_transform import source_discount, STORE_ID, DISCOUNT_ID


def service(database):
    return DiscountSyncService(sessionmaker(bind=database), expected_database=database.url.database,
        store_id=STORE_ID, compatible_only=True)


def test_compatible_sync_resolves_detail_skips_teya_and_preserves_two_existing_discounts(database):
    runner = service(database)
    current, previous = conflicts()
    points = source_discount(id=STORE_ID, type='DISCOUNT_BY_POINTS', discount_percent=None)
    assert run(runner, DiscountAPI([current, points])) == 2
    before = state(database)[0]
    for _ in range(2):
        api = DiscountAPI([previous, unknown(), {**current, 'discount_percent': 10}, points], current)
        assert run(runner, api) == 2
        assert api.requests[-1].url.path.endswith('/discounts/' + DISCOUNT_ID)
        after = state(database)[0]
        assert len(after) == 2 and after[0]._mapping['value'] == 10
        assert all(a._mapping['discount_id'] == b._mapping['discount_id'] and
            a._mapping['created_at'] == b._mapping['created_at'] for a,b in zip(before,after))
        assert all(row._mapping['loyverse_id'] != OTHER_ID for row in after)
        report = runner.last_report
        assert report['partial'] and not report['quarantine_report_written']
        assert report['issue_counts'] == {'DISCOUNT_CURRENT_CONFIRMED': 1, 'DISCOUNT_UNSUPPORTED_TYPE': 1}
        assert any(issue.get('type') == 'TEYA_DISCOUNT' for issue in report['issues'])
        encoded = json.dumps(report)
        for private in [DISCOUNT_ID, OTHER_ID, current['name'], previous['name']]:
            assert private not in encoded


@pytest.mark.parametrize('uppercase', [False, True])
def test_unknown_id_already_stored_rejects_entire_batch_before_dml(database, uppercase):
    runner = service(database)
    assert run(runner, DiscountAPI([source_discount(id=OTHER_ID, discount_percent=5)])) == 1
    if uppercase:
        with database.begin() as db:
            db.exec_driver_sql('UPDATE public."Discounts" SET loyverse_id=upper(loyverse_id)')
    before = state(database)
    with pytest.raises(DiscountSyncError, match='DISCOUNT_OMITTED_ID_EXISTS'):
        run(runner, DiscountAPI([source_discount(discount_percent=10), unknown()]))
    assert state(database) == before and not runner.last_report
    assert runner.last_diagnostics['isolated_ids'] == 1


@pytest.mark.parametrize('status,detail,reason', [
    (404, None, 'DISCOUNT_DETAIL_NOT_FOUND'),
    (200, {'type': 'invalid'}, 'DISCOUNT_DETAIL_INVALID'),
    (200, {**conflicts()[0], 'name': 'Not observed'}, 'DISCOUNT_DETAIL_NOT_OBSERVED'),
])
def test_unresolved_duplicate_blocks_even_when_the_id_is_new(database, status, detail, reason):
    runner = service(database)
    before = state(database)
    valid = source_discount(id=STORE_ID, discount_percent=5)
    with pytest.raises(DiscountSyncError, match='DISCOUNT_UNRESOLVED_CONFLICT'):
        run(runner, DiscountAPI([*conflicts(), valid], detail, status=status))
    assert state(database) == before and not runner.last_report
    assert reason in runner.last_diagnostics['issue_counts']


@pytest.mark.parametrize('status', [401, 403, 429, 503])
def test_provider_error_never_becomes_partial_success(database, status):
    before = state(database)
    with pytest.raises(LoyverseAPIError):
        run(service(database), DiscountAPI(list(conflicts()), {}, status=status))
    assert state(database) == before


@pytest.mark.parametrize('cursor', ['next', {}, True])
def test_incomplete_pagination_blocks_all_changes(database, cursor):
    before = state(database)
    with pytest.raises(LoyverseResponseError):
        run(service(database), DiscountAPI([source_discount(discount_percent=10), unknown()], cursor=cursor))
    assert state(database) == before


def test_malformed_known_discount_blocks_all_changes(database):
    before = state(database)
    with pytest.raises(DiscountValidationError):
        run(service(database), DiscountAPI([source_discount(discount_percent=-1), unknown()]))
    assert state(database) == before


def test_store_and_complete_snapshot_are_required_without_any_omission(database):
    runner = service(database)
    before = state(database)
    snapshot = DiscountSnapshot((parse_discount(source_discount()),), True)
    for changed in [snapshot, replace(snapshot, verified_store_id=STORE_ID, includes_deleted=False)]:
        with pytest.raises(DiscountSyncError, match='tienda verificada'):
            runner.persist_snapshot(changed)
    assert state(database) == before


def test_sql_error_rolls_back_all_valid_rows_without_claiming_success(database):
    runner = service(database)
    before = state(database)[0]
    writes = 0
    def reject_second(connection, cursor, statement, parameters, context, executemany):
        nonlocal writes
        if statement.startswith('INSERT INTO public."Discounts"'):
            writes += 1
            if writes == 2:
                connection.exec_driver_sql('SELECT 1/0')
    event.listen(database, 'before_cursor_execute', reject_second)
    try:
        with pytest.raises(DiscountSyncError, match='persistencia'):
            run(runner, DiscountAPI([source_discount(discount_percent=10),
                source_discount(id=STORE_ID, discount_percent=5), unknown()]))
    finally:
        event.remove(database, 'before_cursor_execute', reject_second)
    assert writes == 2 and state(database)[0] == before and not runner.last_report
    assert runner.last_diagnostics['evidence'] == 'redacted_summary_only'


def test_other_unknown_type_is_not_disclosed_or_coerced(database):
    runner = service(database)
    data = {**unknown(), 'type': 'SYNTHETIC_PRIVATE_TYPE'}
    assert run(runner, DiscountAPI([data])) == 0
    assert state(database)[0] == []
    assert runner.last_report['issues'][0]['type'] == 'OTHER_UNSUPPORTED'
    assert 'SYNTHETIC_PRIVATE_TYPE' not in json.dumps(runner.last_report)


def test_diagnostics_are_bounded_stable_and_contain_no_originals():
    issues = tuple(dict(loyverse_id=f'00000000-0000-4000-8000-{i:012d}',
        reason='DISCOUNT_UNSUPPORTED_TYPE', originals=[{'type': 'TEYA_DISCOUNT', 'secret': 'private'}]) for i in range(25))
    snapshot = DiscountSnapshot((), True, quarantined=issues)
    report = _safe_incidents(snapshot)
    assert len(report['issues']) == 20 and report['issues_truncated']
    assert report['issue_counts'] == {'DISCOUNT_UNSUPPORTED_TYPE': 25}
    assert report == _safe_incidents(replace(snapshot, quarantined=tuple(reversed(issues))))
    assert 'private' not in json.dumps(report)


def test_cli_exposes_safe_incidents_after_commit_and_on_blocked_batch(database, monkeypatch, capsys):
    for key,value in dict(APP_ENV='test', DATABASE_URL=database.url.render_as_string(hide_password=False),
            LOYVERSE_STORE_ID=STORE_ID, LOYVERSE_ACCESS_TOKEN='synthetic-private-token').items():
        monkeypatch.setenv(key,value)
    api = DiscountAPI([*conflicts(), unknown()], conflicts()[0])
    monkeypatch.setattr(cli, 'LoyverseAPIClient', lambda **kwargs: api.client())
    args = ['--expected-database', database.url.database, '--store-id', STORE_ID, '--execute', '--compatible-only']
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'synchronized_compatible_only'
    for secret in ['synthetic-private-token', DISCOUNT_ID, OTHER_ID, STORE_ID, conflicts()[0]['name']]:
        assert secret not in output
    api = DiscountAPI(list(conflicts()), {}, status=404)
    before = state(database)
    assert cli.main(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['code'] == 'DISCOUNT_OMITTED_ID_EXISTS' and report['status'] == 'error'
    assert report['issue_counts'] == {'DISCOUNT_DETAIL_NOT_FOUND': 1}
    assert state(database) == before


def test_manual_strict_policy_still_requires_preserving_the_report(database):
    before = state(database)
    with pytest.raises(DiscountSyncError, match='informe privado'):
        run(strict_service(database), DiscountAPI([*conflicts(), unknown()], conflicts()[0]))
    assert state(database) == before

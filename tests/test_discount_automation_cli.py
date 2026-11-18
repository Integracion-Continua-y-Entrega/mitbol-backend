"""Guardas cloud: sin red, R2, credenciales reales ni conexiones comerciales."""

import json
import os
from unittest.mock import Mock, MagicMock

import pytest

from app.cli import sync_discounts as cli
from test_discount_cli import backend, STORE


@pytest.fixture
def automation(backend, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'cloud')
    monkeypatch.setenv('EXPECTED_DATABASE', cli.PRODUCTION_DATABASE)
    monkeypatch.setenv('EXPECTED_DATABASE_HOST', cli.PRODUCTION_HOST)
    monkeypatch.setenv('DATABASE_URL', f'postgresql://mitbol_discount_worker:synthetic-private-secret@'
        f'{cli.PRODUCTION_HOST}/{cli.PRODUCTION_DATABASE}?sslmode=verify-full&channel_binding=require')
    monkeypatch.delenv('MITBOL_DISCOUNT_SYNC_ENABLED', raising=False)
    backend.connect.return_value = MagicMock()
    monkeypatch.setattr(cli, 'validate_discount_worker_connection', Mock())
    return ['--expected-database', cli.PRODUCTION_DATABASE, '--expected-host', cli.PRODUCTION_HOST,
            '--store-id', STORE, '--require-discount-worker']


def test_preflight_with_disabled_flag_needs_no_loyverse_token_or_storage(automation, capsys):
    assert cli.main([*automation, '--preflight']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['writes'] is False and report['loyverse_contacted'] is False
    assert report['discount_worker_policy'] == 'verified'
    cli.validate_discount_worker_connection.assert_called_once()
    cli.execute.assert_not_awaited()


@pytest.mark.parametrize('flag', [None, 'false', 'TRUE', '1', ''])
def test_automation_disabled_before_any_connection(automation, monkeypatch, capsys, flag):
    if flag is not None:
        monkeypatch.setenv('MITBOL_DISCOUNT_SYNC_ENABLED', flag)
    assert cli.main([*automation, '--execute', '--compatible-only']) == 1
    assert 'DISCOUNT_AUTOMATION_DISABLED' in capsys.readouterr().out
    cli.create_database_engine.assert_not_called()


@pytest.mark.parametrize('change', ['role', 'host', 'database', 'tls'])
def test_automation_rejects_other_destinations_before_connecting(automation, monkeypatch, capsys, change):
    args = automation
    url = cli.CloudDatabaseTarget(cli.PRODUCTION_DATABASE, cli.PRODUCTION_HOST).validate_url(os.environ['DATABASE_URL'])
    if change == 'role':
        url = url.set(username='mitbol_catalog_worker')
    elif change == 'host':
        new = 'ep-temporary.c-8.us-east-2.aws.neon.tech'
        url = url.set(host=new)
        monkeypatch.setenv('EXPECTED_DATABASE_HOST', new)
        args[args.index('--expected-host') + 1] = new
    elif change == 'database':
        url = url.set(database='other_cloud_database')
        monkeypatch.setenv('EXPECTED_DATABASE', url.database)
        args[args.index('--expected-database') + 1] = url.database
    else:
        url = url.update_query_dict({'sslmode': 'require'})
    monkeypatch.setenv('DATABASE_URL', url.render_as_string(hide_password=False))
    assert cli.main([*args, '--preflight']) == 1
    assert 'synthetic-private-secret' not in capsys.readouterr().out
    cli.create_database_engine.assert_not_called()


def test_minimum_policy_failure_prevents_execution(automation, monkeypatch, capsys):
    monkeypatch.setenv('MITBOL_DISCOUNT_SYNC_ENABLED', 'true')
    monkeypatch.setenv('LOYVERSE_ACCESS_TOKEN', 'synthetic-private-token')
    cli.validate_discount_worker_connection.side_effect = cli.DatabaseTargetError('DISCOUNT_WORKER_POLICY_REJECTED')
    assert cli.main([*automation, '--execute', '--compatible-only']) == 1
    cli.execute.assert_not_awaited()
    assert 'DISCOUNT_WORKER_POLICY_REJECTED' in capsys.readouterr().out


def test_enabled_execution_reuses_coordinator_without_quarantine(automation, monkeypatch, capsys):
    monkeypatch.setenv('MITBOL_DISCOUNT_SYNC_ENABLED', 'true')
    monkeypatch.setenv('LOYVERSE_ACCESS_TOKEN', 'synthetic-private-token')
    assert cli.main([*automation, '--execute', '--compatible-only']) == 0
    cli.execute.assert_awaited_once()
    service = cli.execute.call_args.args[0]
    assert service.compatible_only and not service.allow_partial and service.quarantine_dir is None
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'synchronized_compatible_only'
    assert 'synthetic-private-token' not in output and STORE not in output


@pytest.mark.parametrize('extra', [[], ['--preflight'], ['--execute', '--allow-partial'],
                                  ['--execute', '--quarantine-dir', 'private']])
def test_compatible_policy_is_explicit_and_cannot_bypass_manual_quarantine(automation, extra, capsys):
    assert cli.main([*automation, '--compatible-only', *extra]) == 1
    cli.create_database_engine.assert_not_called()


def test_removed_r2_flag_is_rejected_by_parser(automation):
    with pytest.raises(SystemExit) as error:
        cli.main([*automation, '--quarantine-r2'])
    assert error.value.code == 2

"""Offline tests for the exact workflow preflight; no backend or service credentials."""
import contextlib
import io
import os
from pathlib import Path
import sys
import textwrap
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / '.github/workflows/catalog-sync.yml').read_text(encoding='utf-8')
WORKER_SCRIPT = SOURCE.split('          python - <<\'PY\'\n', 1)[1].split('          PY\n', 1)[0]
PREFLIGHT = compile(textwrap.dedent(WORKER_SCRIPT), '<catalog-sync-preflight>', 'exec')
REPOSITORY = 'Integracion-Continua-y-Entrega/mitbol-backend'
HOST = 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'
REVISION = '20261115_0008'
BRANCH = 'br-snowy-king-bd0n9w42'
SENTINEL = 'SYNTHETIC_PRIVATE_TEST_SENTINEL'


def sensitive_error(*, state=None, code=None):
    # Deliberately synthetic, including a complete fake URL, to detect log leaks.
    message = 'postgresql' + '://test_user:' + SENTINEL + '@private.example/database'
    error = RuntimeError(message)
    error.orig = SimpleNamespace(pgcode=state)
    error.code = code
    return error


class DiagnosticTests(unittest.TestCase):
    def run_preflight(self, expected=None, *, missing=(), env_updates=None,
                      branch=BRANCH, heads=None, schema=REVISION, username='mitbol_catalog_worker',
                      host=HOST, database='mitbol_cloud_dev', query=None,
                      fault=None, error=None, runtime_revision=REVISION):
        env = {
            'APP_ENV': 'cloud', 'ADMIN_ENABLED': 'false', 'MITBOL_SYNC_ENABLED': 'false',
            'SYNC_MODE': 'dry-run', 'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
            'EXPECTED_DATABASE': 'mitbol_cloud_dev', 'EXPECTED_DATABASE_HOST': HOST,
            'LOYVERSE_STORE_ID': 'synthetic-store', 'DATABASE_URL': SENTINEL,
            'LOYVERSE_ACCESS_TOKEN': SENTINEL,
        }
        env.update(env_updates or {})
        for name in missing:
            env.pop(name, None)
        url = SimpleNamespace(username=username, host=host, database=database,
            query={'sslmode': 'verify-full', 'channel_binding': 'require'} if query is None else query)
        events = []
        error = error or sensitive_error()

        def maybe_fail(point):
            events.append(point)
            if point in (fault if isinstance(fault, tuple) else (fault,)):
                raise error

        def parse(value):
            maybe_fail('parse')
            return url

        observed_arguments = []
        def configuration(arguments):
            maybe_fail('configuration')
            observed_arguments.append(arguments)
            return url, target, object(), None

        def execute(statement):
            events.append(statement)
            if statement == 'SET TRANSACTION READ ONLY':
                maybe_fail('read_only')
                return None
            if 'neon.branch_id' in statement:
                maybe_fail('identity')
                return SimpleNamespace(scalar_one=lambda: branch)
            if statement == 'SELECT version_num FROM public.alembic_version':
                maybe_fail('revision')
                return SimpleNamespace(all=lambda: [(REVISION,)] if heads is None else heads)
            self.fail('Unexpected SQL in preflight')

        def structural_check(connection):
            maybe_fail('schema')
            return schema

        target = SimpleNamespace(validate_connection=lambda db: maybe_fail('tls'))
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.exec_driver_sql.side_effect = execute
        engine.connect.side_effect = lambda: (maybe_fail('connect') or connection_context)
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection
        connection.begin.return_value.__exit__.side_effect = lambda *args: maybe_fail('transaction_exit')
        engine.dispose.side_effect = lambda: maybe_fail('dispose')
        create = MagicMock(side_effect=lambda *args, **kwargs: (maybe_fail('engine') or engine))
        worker = MagicMock(side_effect=lambda *args: maybe_fail('permissions'))

        modules = {}
        def module(name, **attributes):
            value = ModuleType(name)
            value.__path__ = []
            value.__dict__.update(attributes)
            modules[name] = value

        for name in ('app', 'app.cli', 'app.db', 'app.services', 'sqlalchemy'):
            module(name)
        module('sqlalchemy.engine', make_url=parse)
        module('app.cli.sync_catalog_recurrent', configuration=configuration)
        module('app.db.connection', create_database_engine=create)
        module('app.db.schema_compatibility', validate_bridge_schema=structural_check)
        module('app.db.worker_policy', validate_worker_connection=worker)
        module('app.services.sync_service', CATEGORIES_REVISION=runtime_revision)
        if fault == 'runtime':
            modules['app.cli.sync_catalog_recurrent'] = None
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, modules), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                patch('socket.create_connection', side_effect=AssertionError('Network forbidden')):
            if expected is None:
                exec(PREFLIGHT, {})
                self.assertEqual(output.getvalue(), 'SYNC_PREFLIGHT_OK\n')
            else:
                with self.assertRaises(SystemExit) as stopped:
                    exec(PREFLIGHT, {})
                self.assertEqual(stopped.exception.code, 2)
                self.assertEqual(output.getvalue(), '::error::' + expected + '\n')
        self.assertNotIn(SENTINEL, output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())
        self.assertNotIn('postgresql:', output.getvalue())
        sql = [event for event in events if event.startswith(('SET ', 'SELECT '))]
        self.assertTrue(all(event.startswith(('SET TRANSACTION READ ONLY', 'SELECT ')) for event in sql))
        if sql:
            self.assertEqual(sql[0], 'SET TRANSACTION READ ONLY')
        return SimpleNamespace(create=create, worker=worker, engine=engine,
                               events=events, arguments=observed_arguments)

    def test_success_retains_read_only_identity_schema_and_permission_checks(self):
        run = self.run_preflight()
        run.create.assert_called_once()
        run.worker.assert_called_once()
        run.engine.dispose.assert_called_once()
        self.assertLess(run.events.index('identity'), run.events.index('revision'))
        self.assertLess(run.events.index('revision'), run.events.index('schema'))
        self.assertLess(run.events.index('schema'), run.events.index('permissions'))

    def test_missing_configuration_names_never_values_are_reported(self):
        for name in ('APP_ENV', 'ADMIN_ENABLED', 'EXPECTED_DATABASE', 'EXPECTED_DATABASE_HOST',
                     'LOYVERSE_STORE_ID', 'DATABASE_URL', 'LOYVERSE_ACCESS_TOKEN'):
            with self.subTest(name=name):
                run = self.run_preflight('SYNC_CONFIG_MISSING_' + name, missing=(name,))
                run.create.assert_not_called()

    def test_dry_run_requires_no_approval_even_if_absent_or_malformed(self):
        for updates in ({}, {'MITBOL_RECURRENT_SYNC_APPROVAL': 'malformed-' + SENTINEL}):
            with self.subTest(approval_present=bool(updates)):
                run = self.run_preflight(env_updates=updates)
                self.assertIs(run.arguments[0].execute, False)

    def test_execute_requires_enabled_switch_and_approval_before_connecting(self):
        for mode in ('scheduled', 'process-queue'):
            with self.subTest(mode=mode):
                run = self.run_preflight('SYNC_EXECUTION_DISABLED', env_updates={'SYNC_MODE': mode})
                run.create.assert_not_called()
                run = self.run_preflight('SYNC_CONFIG_MISSING_MITBOL_RECURRENT_SYNC_APPROVAL',
                    env_updates={'SYNC_MODE': mode, 'MITBOL_SYNC_ENABLED': 'true'})
                run.create.assert_not_called()
                run = self.run_preflight(env_updates={'SYNC_MODE': mode, 'MITBOL_SYNC_ENABLED': 'true',
                    'MITBOL_RECURRENT_SYNC_APPROVAL': 'synthetic-approval'})
                self.assertIs(run.arguments[0].execute, True)

    def test_repository_branch_mode_and_cloud_guards_remain_closed(self):
        cases = [
            ('SYNC_WORKFLOW_CONTEXT_INVALID', {'GITHUB_REF': 'refs/heads/develop'}),
            ('SYNC_WORKFLOW_CONTEXT_INVALID', {'GITHUB_REPOSITORY': 'fork/repository'}),
            ('SYNC_MODE_INVALID', {'SYNC_MODE': 'unexpected'}),
            ('SYNC_CONFIG_INVALID', {'APP_ENV': 'local'}),
            ('SYNC_CONFIG_INVALID', {'ADMIN_ENABLED': 'true'}),
        ]
        for code, updates in cases:
            with self.subTest(code=code, names=tuple(updates)):
                run = self.run_preflight(code, env_updates=updates)
                run.create.assert_not_called()

    def test_invalid_runtime_and_configuration_have_safe_diagnostics(self):
        for fault, code in [('runtime', 'SYNC_RUNTIME_CODE_INVALID'),
                            ('parse', 'SYNC_CONFIG_DATABASE_URL_INVALID'),
                            ('configuration', 'SYNC_CONFIG_INVALID')]:
            with self.subTest(fault=fault):
                self.run_preflight(code, fault=fault)
        self.run_preflight('SYNC_RUNTIME_CODE_INVALID', runtime_revision='20261114_0007')

    def test_tls_settings_cannot_be_weakened(self):
        for query in ({}, {'sslmode': 'require'},
                      {'sslmode': 'verify-full', 'channel_binding': 'prefer'}):
            with self.subTest(keys=tuple(query)):
                run = self.run_preflight('SYNC_TLS_CONFIG_INVALID', query=query)
                run.create.assert_not_called()

    def test_full_production_hostname_database_and_role_are_pinned(self):
        for host in ('ep-flat-resonance-bdwb7res.c-8.us-east-2.aws.neon.tech',
                     'ep-green-hill-bddfbmgh-pooler.c-8.us-east-2.aws.neon.tech',
                     'ep-green-hill-bddfbmgh.other.example'):
            with self.subTest(host=host):
                self.run_preflight('SYNC_NEON_TARGET_MISMATCH', host=host)
        self.run_preflight('SYNC_NEON_TARGET_MISMATCH', database='other_database')
        self.run_preflight('SYNC_WORKER_ROLE_MISMATCH', username='neondb_owner')

    def test_missing_or_different_neon_branch_is_rejected(self):
        for branch, code in [(None, 'SYNC_NEON_IDENTITY_UNAVAILABLE'),
                             ('', 'SYNC_NEON_IDENTITY_UNAVAILABLE'),
                             ('br-jolly-wave-bd0qxy1j', 'SYNC_NEON_BRANCH_MISMATCH')]:
            with self.subTest(code=code):
                run = self.run_preflight(code, branch=branch)
                run.worker.assert_not_called()

    def test_revision_and_schema_failures_are_separate(self):
        for heads in ([], [('20261114_0007',)], [('20261116_0009',)],
                      [(REVISION,), (REVISION,)]):
            with self.subTest(heads=heads):
                self.run_preflight('SYNC_ALEMBIC_REVISION_INVALID', heads=heads)
        self.run_preflight('SYNC_SCHEMA_CONTRACT_INVALID', fault='schema')
        self.run_preflight('SYNC_ALEMBIC_REVISION_INVALID', schema='20261114_0007')

    def test_each_exception_stage_hides_sensitive_error_text(self):
        cases = [('engine', 'SYNC_CONNECTION_TLS_FAILED'), ('connect', 'SYNC_CONNECTION_TLS_FAILED'),
                 ('read_only', 'SYNC_READ_ONLY_FAILED'), ('tls', 'SYNC_CONNECTION_TLS_FAILED'),
                 ('identity', 'SYNC_NEON_IDENTITY_UNAVAILABLE'), ('revision', 'SYNC_ALEMBIC_REVISION_INVALID'),
                 ('schema', 'SYNC_SCHEMA_CONTRACT_INVALID'), ('permissions', 'SYNC_WORKER_PERMISSIONS_INVALID'),
                 ('transaction_exit', 'SYNC_READ_ONLY_TRANSACTION_FAILED'),
                 ('dispose', 'SYNC_CONNECTION_CLEANUP_FAILED')]
        for fault, code in cases:
            with self.subTest(fault=fault):
                self.run_preflight(code, fault=fault)

    def test_structured_authentication_permissions_and_tls_codes_are_sanitized(self):
        cases = [
            ('connect', sensitive_error(state='28P01'), 'SYNC_DB_AUTH_FAILED'),
            ('connect', sensitive_error(state='28000'), 'SYNC_DB_AUTH_FAILED'),
            ('revision', sensitive_error(state='42501'), 'SYNC_WORKER_PERMISSIONS_INVALID'),
            ('permissions', sensitive_error(state='42501'), 'SYNC_WORKER_PERMISSIONS_INVALID'),
        ]
        for target_code in ('TLS_NOT_ACTIVE', 'TLS_MODE_MISMATCH', 'CHANNEL_BINDING_MISMATCH', 'TLS_CA_MISMATCH'):
            cases.append(('connect', sensitive_error(code=target_code), 'SYNC_TLS_VALIDATION_FAILED'))
        for target_code in ('EFFECTIVE_HOST_MISMATCH', 'EFFECTIVE_DATABASE_MISMATCH',
                            'EFFECTIVE_PORT_MISMATCH', 'EFFECTIVE_USER_MISMATCH', 'EFFECTIVE_OPTIONS_REJECTED'):
            cases.append(('connect', sensitive_error(code=target_code), 'SYNC_NEON_TARGET_MISMATCH'))
        cases.append(('connect', sensitive_error(code=SENTINEL), 'SYNC_CONNECTION_TLS_FAILED'))
        for fault, error, code in cases:
            with self.subTest(code=code):
                self.run_preflight(code, fault=fault, error=error)

    def test_original_failure_is_not_hidden_by_cleanup_failure(self):
        # Two failures: the permission diagnosis must survive a dispose exception.
        run = self.run_preflight('SYNC_WORKER_PERMISSIONS_INVALID', fault=('permissions', 'dispose'))
        run.engine.dispose.assert_called_once()

    def test_production_job_guard_cli_flags_and_isolated_pr_validation(self):
        block = SOURCE.split('  synchronize:\n', 1)[1].split('    runs-on:', 1)[0]
        expression = block.split('    if: >-\n', 1)[1]
        expression = ' '.join(expression.split()).removeprefix('${{').removesuffix('}}').strip()
        expression = expression.replace('&&', ' and ').replace('||', ' or ')
        def allowed(event, mode, enabled, ref='refs/heads/main', repository=REPOSITORY):
            return bool(eval(expression, {'__builtins__': {}}, {
                'github': SimpleNamespace(repository=repository, ref=ref, event_name=event),
                'inputs': SimpleNamespace(mode=mode), 'vars': SimpleNamespace(MITBOL_SYNC_ENABLED=enabled),
            }))
        self.assertTrue(allowed('workflow_dispatch', 'dry-run', 'false'))
        self.assertFalse(allowed('schedule', 'dry-run', 'false'))
        self.assertTrue(allowed('schedule', '', 'true'))
        self.assertFalse(allowed('workflow_dispatch', 'process-queue', 'false'))
        self.assertTrue(allowed('workflow_dispatch', 'process-queue', 'true'))
        self.assertFalse(allowed('pull_request', 'dry-run', 'true', ref='refs/pull/1/merge'))
        self.assertFalse(allowed('workflow_dispatch', 'dry-run', 'true', repository='fork/repository'))
        self.assertIn('ref: develop', SOURCE)
        self.assertIn("- cron: '*/5 * * * *'", SOURCE)
        self.assertIn('cancel-in-progress: false', SOURCE)
        self.assertIn('dry-run)       args+=(--dry-run)', SOURCE)
        self.assertIn('process-queue) args+=(--execute)', SOURCE)
        self.assertIn('scheduled)     args+=(--execute --enqueue-scheduled)', SOURCE)
        self.assertIn('python -m app.cli.sync_catalog_jobs "${args[@]}"', SOURCE)
        self.assertIn('permissions:\n  contents: read\n', SOURCE)
        diagnostics = SOURCE.split('  diagnostics:\n', 1)[1]
        self.assertIn("if: github.event_name == 'pull_request'", diagnostics)
        self.assertNotIn('secrets.', diagnostics)
        self.assertNotIn('DATABASE_URL', diagnostics)


if __name__ == '__main__':
    unittest.main(verbosity=2)

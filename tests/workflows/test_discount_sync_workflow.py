"""Offline orchestration and diagnostics tests; no backend credentials or services."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / '.github/workflows/catalog-sync.yml').read_text(encoding='utf-8')
JOB = SOURCE.split('  discounts:\n', 1)[1].split('  diagnostics:\n', 1)[0]
SCRIPT = textwrap.dedent(JOB.split('        run: |\n', 2)[2])
SUMMARY = compile(textwrap.dedent(JOB.split('          # DISCOUNT_SUMMARY_BEGIN\n', 1)[1].split(
    '          # DISCOUNT_SUMMARY_END', 1)[0]), '<discount-summary>', 'exec')
SENTINEL = 'SYNTHETIC_PRIVATE_SENTINEL'


def report(**changes):
    return dict(status='synchronized_compatible_only', processed=2, source_records=4,
        valid_records=2, isolated_ids=1, resolved_ids=1,
        issue_counts={'DISCOUNT_UNSUPPORTED_TYPE': 1, 'DISCOUNT_CURRENT_CONFIRMED': 1},
        issues=[dict(code='DISCOUNT_UNSUPPORTED_TYPE', reference='abcdef0123456789', type='TEYA_DISCOUNT')],
        **changes)


class DiscountWorkflowTests(unittest.TestCase):
    def test_existing_catalog_job_is_byte_for_byte_unchanged(self):
        catalog = SOURCE.split('  synchronize:\n', 1)[1].split('  discounts:\n', 1)[0]
        self.assertEqual(hashlib.sha256(catalog.encode()).hexdigest(), '80e9dfb26f8ce8cfea7dfba74dbe45b036608992a1a1f1e0112b8fbf27b22986')

    def test_flag_defaults_disabled_and_manual_preflight_remains_explicit(self):
        self.assertIn("vars.MITBOL_DISCOUNT_SYNC_ENABLED || 'false'", JOB)
        self.assertIn("vars.MITBOL_DISCOUNT_SYNC_ENABLED == 'true'", JOB)
        self.assertIn('inputs.discount_preflight', JOB)
        self.assertIn('        default: false\n        type: boolean', SOURCE)
        self.assertIn("needs.synchronize.result == 'success'", JOB)
        self.assertIn('needs: synchronize', JOB)
        self.assertIn("github.ref == 'refs/heads/main'", JOB)
        self.assertIn('ref: develop', JOB)
        self.assertIn('cancel-in-progress: false', SOURCE)

    def test_only_two_independent_secrets_without_r2_or_schema_changes(self):
        secrets = [line.split('secrets.', 1)[1].split()[0] for line in JOB.splitlines() if 'secrets.' in line]
        self.assertEqual(secrets, ['DISCOUNT_DATABASE_URL', 'DISCOUNT_LOYVERSE_ACCESS_TOKEN'])
        for forbidden in ('MITBOL_RECURRENT_SYNC_APPROVAL', 'DISCOUNT_QUARANTINE', '--quarantine',
                          '--allow-partial', 'upload-artifact', 'alembic upgrade', 'aws s3',
                          'set -x', 'continue-on-error'):
            self.assertNotIn(forbidden, JOB)
        self.assertIn('permissions:\n  contents: read', SOURCE)

    def run_script(self, mode, enabled='false', fail_preflight=False, execute_code=0):
        bash = r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else shutil.which('bash')
        self.assertTrue(bash and Path(bash).is_file(), 'Bash is required to test exact commands')
        with tempfile.TemporaryDirectory(prefix='mitbol-discount-workflow-') as directory:
            root = Path(directory)
            prefix = r"""set -e
python() {
  if [ "$1" = '-' ]; then "$MITBOL_TEST_PYTHON" "$@"; return $?; fi
  printf '%s\n' "$*" >> calls.txt
  case "$*" in
    *--preflight*)
      if [ "$MITBOL_TEST_FAIL_PREFLIGHT" = 'true' ]; then return 1; fi
      return 0 ;;
  esac
  printf '%s\n' "$MITBOL_TEST_CLI_REPORT"
  return "$MITBOL_TEST_EXECUTE_CODE"
}
"""
            data = report()
            if execute_code:
                data.update(status='error', code='DISCOUNT_UNRESOLVED_CONFLICT', message=SENTINEL)
            script = root / 'run.sh'
            script.write_text(prefix + SCRIPT, encoding='utf-8', newline='\n')
            summary = root / 'summary.md'
            env = {**os.environ, 'DISCOUNT_MODE': mode, 'MITBOL_DISCOUNT_SYNC_ENABLED': enabled,
                'EXPECTED_DATABASE': 'synthetic', 'EXPECTED_DATABASE_HOST': 'synthetic.invalid',
                'LOYVERSE_STORE_ID': 'synthetic-store', 'RUNNER_TEMP': root.as_posix(),
                'GITHUB_STEP_SUMMARY': summary.as_posix(), 'MITBOL_TEST_PYTHON': Path(sys.executable).as_posix(),
                'MITBOL_TEST_FAIL_PREFLIGHT': 'true' if fail_preflight else 'false',
                'MITBOL_TEST_CLI_REPORT': json.dumps(data), 'MITBOL_TEST_EXECUTE_CODE': str(execute_code)}
            result = subprocess.run([str(bash), script.name], cwd=root, env=env, capture_output=True, text=True)
            calls = (root / 'calls.txt').read_text().splitlines() if (root / 'calls.txt').exists() else []
            return result, calls, summary.read_text() if summary.exists() else ''

    def test_manual_preflight_never_executes_even_with_enabled_flag(self):
        for flag in ('false', 'true'):
            result, calls, summary = self.run_script('dry-run', flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(calls), 1)
            self.assertIn('--preflight', calls[0])
            self.assertNotIn('--execute', calls[0])
            self.assertIn('--require-discount-worker', calls[0])
            self.assertEqual(summary, '')

    def test_execution_requires_flag_and_preflight_before_one_import(self):
        for mode in ('scheduled', 'process-queue'):
            result, calls, summary = self.run_script(mode, 'true')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(calls), 2)
            self.assertIn('--preflight', calls[0])
            self.assertIn('--execute --compatible-only', calls[1])
            self.assertIn('DISCOUNT_UNSUPPORTED_TYPE', summary)
            self.assertIn('::warning::DISCOUNT_UNSUPPORTED_TYPE count=1', result.stdout)
            self.assertIn('::notice::DISCOUNT_DUPLICATES_RESOLVED count=1', result.stdout)
            result, calls, summary = self.run_script(mode)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(calls, [])
            self.assertEqual(summary, '')

    def test_failed_preflight_and_invalid_mode_do_not_execute(self):
        result, calls, _ = self.run_script('scheduled', 'true', fail_preflight=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('--execute', calls[0])
        result, calls, _ = self.run_script('invalid', 'true')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_import_failure_is_not_hidden_and_is_not_retried(self):
        result, calls, summary = self.run_script('scheduled', 'true', execute_code=1)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn('::error::DISCOUNT_UNRESOLVED_CONFLICT', result.stdout)
        self.assertNotIn(SENTINEL, result.stdout + result.stderr + summary)

    def render(self, data, *, raw=False):
        with tempfile.TemporaryDirectory(prefix='mitbol-discount-summary-') as directory:
            root = Path(directory)
            file = root / 'report.json'
            file.write_text(data if raw else json.dumps(data), encoding='utf-8')
            summary = root / 'summary.md'
            output = io.StringIO()
            code = 0
            with patch.dict(os.environ, {'DISCOUNT_RESULT_FILE': str(file), 'GITHUB_STEP_SUMMARY': str(summary)}), contextlib.redirect_stdout(output):
                try:
                    exec(SUMMARY, {})
                except SystemExit as error:
                    code = error.code
            return code, output.getvalue(), summary.read_text() if summary.exists() else ''

    def test_summary_whitelists_fields_and_never_exposes_payloads_or_exception_messages(self):
        data = report()
        data.update(name=SENTINEL, token=SENTINEL, database_url=SENTINEL, originals=[SENTINEL])
        data['issues'][0]['secret'] = SENTINEL
        data['issue_counts'][SENTINEL] = 1
        code, output, summary = self.render(data)
        self.assertEqual(code, 0)
        self.assertNotIn(SENTINEL, output + summary)
        self.assertIn('abcdef0123456789', summary)
        self.assertIn('TEYA_DISCOUNT', summary)

    def test_bad_result_reports_only_safe_code(self):
        for data in (SENTINEL, '[]', json.dumps(dict(status=SENTINEL)),
                     json.dumps({**report(), 'processed': SENTINEL}),
                     json.dumps({**report(), 'issues': [dict(code='DISCOUNT_UNSUPPORTED_TYPE', reference=SENTINEL)]})):
            code, output, summary = self.render(data, raw=True)
            self.assertEqual(code, 2)
            self.assertIn('::error::DISCOUNT_RESULT_INVALID_OR_UNAVAILABLE', output)
            self.assertNotIn(SENTINEL, output + summary)

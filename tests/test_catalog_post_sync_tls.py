"""Bloque C: TLS del mismo psql y diagnóstico backend/proxy separados, todo simulado."""

import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize('case', [
    'proxy_false', 'ssl_false', 'tls12', 'missing_conninfo', 'missing_protocol',
    'wrong_client_user', 'wrong_host', 'wrong_database', 'wrong_port',
    'sql_identity_failure', 'sql_readonly_failure', 'sql_catalog_failure',
])
def test_block_c_requires_client_tls_and_preserves_sql_failures(case, tmp_path):
    shell = shutil.which('powershell.exe') or shutil.which('pwsh')
    if not shell:
        pytest.skip('Requiere PowerShell')
    root = Path(__file__).resolve().parents[1]
    doc = (root / 'docs/loyverse/neon_stage3e_execute.md').read_text(encoding='utf-8')
    block = re.findall(r'```powershell\s*\n(.*?)```', doc, re.S)[2]
    code = (root / 'docs/loyverse/verify_neon_after_sync.ps1').read_text(encoding='utf-8-sig')
    assert '--execute' not in code + block and 'sync_catalog' not in code + block
    for control in ('sslmode=verify-full', 'channel_binding=require', "sslrootcert='$quotedNeonCa'",
                    'gssencmode=disable', 'ssl_min_protocol_version=TLSv1.3', '-X -W', 'ON_ERROR_STOP=1'):
        assert control in code
    ca = tmp_path / 'public-test-ca.pem'
    ca.write_text('synthetic public CA placeholder', encoding='ascii')
    def quoted(path):
        return str(path).replace("'", "''")
    block = block.replace(r'Set-Location C:\Proyectos\mitbol\mitbol-backend', f"Set-Location -LiteralPath '{quoted(root)}'")
    block = block.replace(r'& .\docs\loyverse\verify_neon_after_sync.ps1', '& ([scriptblock]::Create($helper))')
    code = code.replace("Set-Location -LiteralPath (Join-Path $PSScriptRoot '..\\..')", f"Set-Location -LiteralPath '{quoted(root)}'")
    code = re.sub(r"(?m)^\$neonPsql = '[^\n]+'$", lambda _: f"$neonPsql = '{quoted(Path(sys.executable))}'", code)
    code, n = re.subn(r'(?m)^\$psqlVersion = & \$neonPsql --version$', "$psqlVersion = 'psql (PostgreSQL) 18.2'; $global:LASTEXITCODE = 0", code)
    assert n == 1
    code, n = re.subn(r'(?m)^\$neonCa = & [^\n]+$', lambda _: f"$neonCa = '{quoted(ca)}'; $global:LASTEXITCODE = 0", code)
    assert n == 1
    code, n = re.subn(r'(?m)^\s*\$auditOutput = @\(& [^\n]+$', '\n        $auditOutput = @(Invoke-FakePsql -Login $login)', code)
    assert n == 1
    encoded = base64.b64encode(code.encode('utf-8')).decode('ascii')
    program = r"""
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$case='__CASE__'
$env:APP_ENV='cloud'; $env:ADMIN_ENABLED='false'
$env:EXPECTED_DATABASE='mitbol_cloud_dev'
$env:EXPECTED_DATABASE_HOST='ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'
$env:DATABASE_URL='PRIVATE_DATABASE_VALUE_NEVER_OUTPUT'
$env:LC_ALL='previous-locale'; $env:LC_MESSAGES='previous-messages'
$env:MITBOL_SYNC_APPROVED_MANIFEST='previous-public-manifest'
$env:MITBOL_SYNC_APPROVED_MANIFEST_B64='previous-base64-manifest'
$originalEncoding=[Console]::OutputEncoding
$global:mitbolNeonApprovedPreflight=[pscustomobject]@{catalog_manifest=@{store=@{name=('Ch'+[char]0xed+'a')}}}
$global:sqlCalls=0
function Invoke-FakePsql { param($Login)
    $global:sqlCalls++
    $global:LASTEXITCODE=0
    if ($case -like 'sql_*_failure') {
        $global:LASTEXITCODE=3
        return 'safe SQL failure'
    }
    if ($env:LC_ALL -cne 'C' -or $env:LC_MESSAGES -cne 'C') { throw 'Non-deterministic conninfo locale' }
    if ($env:MITBOL_SYNC_APPROVED_MANIFEST_B64 -match '[^\x00-\x7f]') { throw 'Non-ASCII manifest transport' }
    $manifest=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:MITBOL_SYNC_APPROVED_MANIFEST_B64)) | ConvertFrom-Json
    if ($manifest.store.name -cne ('Ch'+[char]0xed+'a')) { throw 'Unicode manifest changed' }
    if ([Console]::OutputEncoding.CodePage -ne 65001) { throw 'Native output must be decoded as UTF8' }
    if ($case -eq 'missing_conninfo') { return 'catalog_data_verified_after_sync' }
    $ssl=if($case -eq 'ssl_false'){'false'}else{'true'}
    $protocol=if($case -eq 'tls12'){'TLSv1.2'}else{'TLSv1.3'}
    $hostName=if($case -eq 'wrong_host'){'other.example.com'}else{'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'}
    $dbName=if($case -eq 'wrong_database'){'another_database'}else{'mitbol_cloud_dev'}
    $clientUser=if($case -eq 'wrong_client_user'){'another_user'}else{$Login}
    $portNumber=if($case -eq 'wrong_port'){'6432'}else{'5432'}
    'MITBOL_CLIENT_CONNINFO_BEGIN'
    'Parameter,Value'
    "Database,$dbName"; "Client User,$clientUser"; "Host,$hostName"; "Server Port,$portNumber"
    "SSL Connection,$ssl"
    if($case -ne 'missing_protocol'){ "SSL Protocol,$protocol" }
    'MITBOL_CLIENT_CONNINFO_END'
    'backend_ssl_observed=false backend_tls_version_observed=NULL'
    'catalog_data_verified_after_sync'
}
$helper=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CODE__'))
$code=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__BLOCK__'))
$tokens=$null; $errors=$null
[Management.Automation.Language.Parser]::ParseInput($helper,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count -ne 0){throw 'Invalid helper syntax'}
[Management.Automation.Language.Parser]::ParseInput($code,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count -ne 0){throw 'Invalid block C syntax'}
$failed=$false
try { & ([scriptblock]::Create($code)) } catch { $failed=$true }
if($failed -ne ($case -ne 'proxy_false')){throw 'Unexpected client/SQL audit outcome'}
$expectedCalls=if($case -eq 'proxy_false'){2}else{1}
if($global:sqlCalls -ne $expectedCalls){throw 'Wrong login flow'}
if($env:LC_ALL -cne 'previous-locale' -or $env:LC_MESSAGES -cne 'previous-messages' -or $env:MITBOL_SYNC_APPROVED_MANIFEST -cne 'previous-public-manifest'){throw 'Audit environment not restored'}
if($env:MITBOL_SYNC_APPROVED_MANIFEST_B64 -cne 'previous-base64-manifest' -or [Console]::OutputEncoding.CodePage -ne $originalEncoding.CodePage){throw 'Manifest/encoding not restored'}
Write-Output 'block_c_client_tls_mock_ok'
""".replace('__CASE__', case).replace('__CODE__', encoded).replace('__BLOCK__', base64.b64encode(block.encode('utf-8')).decode('ascii'))
    env = {k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','TEMP','TMP','COMSPEC'}}
    process = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command',
        '. ([scriptblock]::Create([Console]::In.ReadToEnd()))'], input=program, env=env,
        capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert 'block_c_client_tls_mock_ok' in process.stdout
    assert 'PRIVATE_DATABASE_VALUE_NEVER_OUTPUT' not in process.stdout + process.stderr
    if case == 'proxy_false':
        assert process.stdout.count('catalog_verified_after_sync') == 2

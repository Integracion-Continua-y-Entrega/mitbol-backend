"""Los bloques manuales nunca alcanzan Execute ante cambios o segundos intentos."""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('mode', ['success', 'changed', 'counts', 'execute_error'])
@pytest.mark.parametrize('previous', [False, True])
def test_documented_manual_workflow_is_single_attempt_and_restores_secrets(mode, previous):
    shell = shutil.which('powershell.exe') or shutil.which('pwsh')
    if not shell:
        pytest.skip('Requiere PowerShell')
    root = Path(__file__).resolve().parents[1]
    doc = root / 'docs/loyverse/neon_stage3e_execute.md'
    blocks = re.findall(r'```powershell\s*\n(.*?)```', doc.read_text(encoding='utf-8'), re.S)
    assert len(blocks) == 3
    expression = r'(?m)^\s*\$cliOutput = & \.\\\.venv\\Scripts\\python\.exe[^\r\n]*'
    assert len(re.findall(expression, blocks[0])) == 1
    # No arrancar la CLI real ni ninguna conexión; C sólo se analiza sintácticamente.
    blocks[0] = re.sub(expression, '\n    $cliOutput = Invoke-MockedCLI -Mode $modeFlag', blocks[0])
    blocks[0] = blocks[0].replace(
        r'Set-Location C:\Proyectos\mitbol\mitbol-backend',
        "Set-Location -LiteralPath '" + str(root).replace("'", "''") + "'",
    )
    assert '.venv\\Scripts\\python.exe' not in blocks[0]
    report = dict(status='ready_for_initial_sync', writes=False, loyverse_contacted=True,
                  app_env='cloud', admin_enabled=False, user='neondb_owner', database='mitbol_cloud_dev',
                  host='ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech', revision='20261102_0003',
                  tls=dict(protocol='TLSv1.3', sslmode='verify-full', channel_binding='require'),
                  store=dict(id='9e30730f-a5b4-4485-8cc4-bc499cc6e239', name='I.C.E'),
                  write_permissions_verified=True, default_transaction_read_only='off',
                  table_counts={name: 0 for name in ['Products','ProductVariants','ProductModifiers',
                      'ProductComplements','ProductModifierAssignments','ProductImages','Tags','ProductTags']},
                  planned_initial_inserts=dict(Products=3,ProductVariants=5,ProductModifiers=2,
                      ProductComplements=5,ProductModifierAssignments=4),
                  commercially_available_variants=5, commercially_unavailable_variants=0,
                  tombstones=dict(products=2,variants=0,modifiers=0,complements=0),
                  catalog_sha256='a' * 64, catalog_manifest={'store': {'name': 'I.C.E'}})
    def encoded(value):
        return base64.b64encode(value.encode('utf-8')).decode('ascii')
    program = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$case = '__MODE__'
$env:APP_ENV = 'cloud'; $env:ADMIN_ENABLED = 'false'
$env:DATABASE_URL = 'configured-for-mock-only'
$env:EXPECTED_DATABASE = 'mitbol_cloud_dev'
$env:EXPECTED_DATABASE_HOST = 'ep-green-hill-bddfbmgh.c-8.us-east-2.aws.neon.tech'
$env:LOYVERSE_STORE_ID = '9e30730f-a5b4-4485-8cc4-bc499cc6e239'
$oldToken = if (__PREVIOUS__) { 'fake-before-token' } else { $null }
$oldApproval = if (__PREVIOUS__) { 'fake-before-approval' } else { $null }
$env:LOYVERSE_ACCESS_TOKEN = $oldToken
$env:MITBOL_SYNC_APPROVED_PREFLIGHT = $oldApproval
$global:pfCalls = 0; $global:executeCalls = 0
function Read-Host { param($Prompt, [switch]$AsSecureString)
    if (-not $AsSecureString) { throw 'Unhidden prompt' }
    return (ConvertTo-SecureString 'fake-manual-token' -AsPlainText -Force)
}
function Invoke-MockedCLI { param($Mode)
    if ($env:LOYVERSE_ACCESS_TOKEN -cne 'fake-manual-token') { throw 'Missing token assignment' }
    $global:LASTEXITCODE = 0
    if ($Mode -eq '--execute') {
        $global:executeCalls++
        if ($env:MITBOL_SYNC_APPROVED_PREFLIGHT -ceq 'fake-before-approval') { throw 'Missing approval' }
        if ($case -eq 'execute_error') { $global:LASTEXITCODE=1; return '{"status":"error","message":"safe simulated failure"}' }
        return '{"status":"synchronized","writes":true}'
    }
    if ($Mode -ne '--preflight') { throw 'Unexpected command mode' }
    $global:pfCalls++
    $r = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__REPORT__')) | ConvertFrom-Json
    if ($case -eq 'changed' -and $global:pfCalls -eq 2) { $r.catalog_sha256 = 'b' * 64 }
    if ($case -eq 'counts') { $r.planned_initial_inserts.Products = 4 }
    return ($r | ConvertTo-Json -Depth 20 -Compress)
}
$allBlocks = @('__A__','__B__','__C__') | ForEach-Object { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($_)) }
foreach ($text in $allBlocks) {
    $tokens=$null; $errors=$null
    [Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors) | Out-Null
    if ($errors.Count -ne 0) { throw 'Invalid PowerShell syntax' }
}
$failed=$false
try {
    . ([scriptblock]::Create($allBlocks[0]))
    . ([scriptblock]::Create($allBlocks[1]))
} catch { $failed=$true }
if ($failed -ne ($case -ne 'success')) { throw 'Unexpected outcome' }
$expectedExecutions = if ($case -in @('success','execute_error')) { 1 } else { 0 }
if ($global:executeCalls -ne $expectedExecutions) { throw 'Execution escaped gate' }
if ($env:LOYVERSE_ACCESS_TOKEN -cne $oldToken) { throw 'Previous token not restored' }
if ($env:MITBOL_SYNC_APPROVED_PREFLIGHT -cne $oldApproval) { throw 'Previous approval not restored' }
if (-not __PREVIOUS__) {
    if (Test-Path -LiteralPath Env:LOYVERSE_ACCESS_TOKEN) { throw 'Previously absent token still exists' }
    if (Test-Path -LiteralPath Env:MITBOL_SYNC_APPROVED_PREFLIGHT) { throw 'Previously absent approval still exists' }
}
if ($case -ne 'counts') {
    $blocked=$false
    try { . ([scriptblock]::Create($allBlocks[1])) } catch { $blocked=$true }
    if (-not $blocked -or $global:executeCalls -ne $expectedExecutions) { throw 'Second attempt allowed' }
}
Write-Output 'manual_workflow_mock_ok'
"""
    for marker, value in {'__MODE__': mode, '__PREVIOUS__': '$true' if previous else '$false', '__REPORT__': encoded(json.dumps(report)),
                          '__A__': encoded(blocks[0]), '__B__': encoded(blocks[1]), '__C__': encoded(blocks[2])}.items():
        program = program.replace(marker, value)
    child_env = {k:v for k,v in os.environ.items()
                 if k.upper() in {'SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','TEMP','TMP','COMSPEC'}}
    result = subprocess.run([shell,'-NoProfile','-NonInteractive','-Command',
        '. ([scriptblock]::Create([Console]::In.ReadToEnd()))'], input=program,
        env=child_env, capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stderr
    assert 'manual_workflow_mock_ok' in result.stdout
    assert 'fake-manual-token' not in result.stdout + result.stderr
    assert 'fake-before-token' not in result.stdout + result.stderr

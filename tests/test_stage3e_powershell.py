"""Ejecuta el bloque documentado con entrada falsa y sin acceso a servicios."""

import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


DOCUMENT = Path(__file__).resolve().parents[1] / "docs/loyverse/neon_stage3e.md"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


@pytest.mark.skipif(POWERSHELL is None, reason="Requiere PowerShell")
@pytest.mark.parametrize("previous", [False, True])
@pytest.mark.parametrize("mode", ["success", "nonzero", "exception", "empty", "allocation_error", "conversion_error"])
def test_documented_token_reaches_child_and_is_restored(previous, mode):
    block = re.findall(r"```powershell\s*\n(.*?)```", DOCUMENT.read_text(encoding="utf-8"), re.S)[1]
    # Nunca ejecutar la CLI real: sustituir exclusivamente su única invocación.
    invocation = r"(?m)^\s*& \.\\\.venv\\Scripts\\python\.exe[^\r\n]*"
    commands = re.findall(invocation, block)
    assert len(commands) == 1 and "--preflight" in commands[0] and "--execute" not in commands[0]
    block = re.sub(invocation, "\n    Invoke-MockedPreflight", block)
    assert "app.cli.sync_catalog" not in block
    # Contar liberaciones sin permitir un double-free real en una regresión.
    for method, wrapper, argument in (
        ("SecureStringToBSTR", "New-TestBSTR", "$loyverseSecret"),
        ("PtrToStringBSTR", "Read-TestBSTR", "$loyversePointer"),
        ("ZeroFreeBSTR", "Free-TestBSTR", "$loyversePointer"),
    ):
        original = f"[Runtime.InteropServices.Marshal]::{method}({argument})"
        assert original in block
        block = block.replace(original, f"({wrapper} {argument})")
    encoded_block = base64.b64encode(block.encode("utf-8")).decode("ascii")
    python_path = str(Path(sys.executable)).replace("'", "''")
    program = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$mode = '__MODE__'
$oldValue = if (__PREVIOUS__) { 'stage3e-fake-previous' } else { $null }
$env:LOYVERSE_ACCESS_TOKEN = $oldValue
if ($null -eq $oldValue -and (Test-Path -LiteralPath Env:LOYVERSE_ACCESS_TOKEN)) { throw 'Test setup did not remove variable' }
$env:LOYVERSE_STORE_ID = 'stage3e-fake-store'
$script:preflightCalls = 0
$script:freeCalls = 0
$script:activeBSTR = [IntPtr]::Zero

function New-TestBSTR {
    param([Security.SecureString]$Secret)
    if ($mode -eq 'allocation_error') { throw 'Simulated allocation failure' }
    $script:activeBSTR = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    return $script:activeBSTR
}

function Read-TestBSTR {
    param([IntPtr]$Pointer)
    if ($mode -eq 'conversion_error') { throw 'Simulated conversion failure' }
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
}

function Free-TestBSTR {
    param([IntPtr]$Pointer)
    if ($Pointer -eq [IntPtr]::Zero -or $Pointer -ne $script:activeBSTR) { throw 'Repeated or invalid BSTR free' }
    $script:freeCalls++
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    $script:activeBSTR = [IntPtr]::Zero
}

function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    if (-not $AsSecureString) { throw 'Unexpected unhidden prompt' }
    $secret = New-Object System.Security.SecureString
    if ($mode -ne 'empty') {
        foreach ($character in 'stage3e-fake-new-token'.ToCharArray()) { $secret.AppendChar($character) }
    }
    return $secret
}

function Invoke-MockedPreflight {
    $script:preflightCalls++
    if ($env:LOYVERSE_ACCESS_TOKEN -cne 'stage3e-fake-new-token') { throw 'Token not assigned' }
    if ($loyversePointer -ne [IntPtr]::Zero) { throw 'BSTR not freed before child' }
    # Proceso hijo real, sin importar app, HTTP ni PostgreSQL. Sólo compara en memoria.
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = '__PYTHON__'
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.Arguments = '-I -B -c "import os, sys; sys.exit(0 if os.environ.get(''LOYVERSE_ACCESS_TOKEN'') == ''stage3e-fake-new-token'' else 31)"'
    $child = [Diagnostics.Process]::Start($start)
    try {
        $child.WaitForExit()
        if ($child.ExitCode -ne 0) { throw 'Child did not inherit token' }
    } finally { $child.Dispose() }
    if ($mode -eq 'exception') { throw 'Simulated launch failure' }
    $global:LASTEXITCODE = if ($mode -eq 'nonzero') { 91 } else { 0 }
    Write-Output 'safe_mock_preflight_output'
}

$code = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__BLOCK__'))
$parseTokens = $null
$parseErrors = $null
[Management.Automation.Language.Parser]::ParseInput($code, [ref]$parseTokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -ne 0) { throw 'Invalid documented PowerShell syntax' }
$failed = $false
$failureMessage = $null
try { . ([scriptblock]::Create($code)) } catch { $failed = $true; $failureMessage = $_.Exception.Message }
if ($failed -ne ($mode -ne 'success')) { throw "Unexpected outcome: $failureMessage" }
if ($mode -eq 'exception' -and $failureMessage -cne 'Simulated launch failure') { throw 'Wrong launch error' }
if ($mode -eq 'nonzero' -and $failureMessage -cne 'Falló el preflight completo. No autorizar escrituras.') { throw 'Wrong preflight error' }
if ($mode -eq 'empty' -and $failureMessage -cne 'El token no puede estar vacío.') { throw 'Wrong empty-input error' }
if ($mode -eq 'allocation_error' -and $failureMessage -cne 'Simulated allocation failure') { throw 'Wrong allocation error' }
if ($mode -eq 'conversion_error' -and $failureMessage -cne 'Simulated conversion failure') { throw 'Wrong conversion error' }
$expectedCalls = if ($mode -in @('empty', 'allocation_error', 'conversion_error')) { 0 } else { 1 }
if ($script:preflightCalls -ne $expectedCalls) { throw 'Unexpected call count' }
$expectedFrees = if ($mode -in @('empty', 'allocation_error')) { 0 } else { 1 }
if ($script:freeCalls -ne $expectedFrees -or $script:activeBSTR -ne [IntPtr]::Zero) { throw 'BSTR not freed exactly once' }
$restored = [Environment]::GetEnvironmentVariable('LOYVERSE_ACCESS_TOKEN', 'Process')
if ($null -eq $oldValue) {
    if (Test-Path -LiteralPath Env:LOYVERSE_ACCESS_TOKEN) { throw 'Previously absent variable still exists' }
    if ($null -ne $restored) { throw 'Previously absent variable not removed' }
} elseif ($restored -cne $oldValue) { throw 'Previous variable not restored' }
if ($null -ne $previousLoyverseToken -or $null -ne $loyverseSecret -or $loyversePointer -ne [IntPtr]::Zero) {
    throw 'Temporary references not cleared'
}
Write-Output 'stage3e_powershell_ok'
"""
    program = (program.replace("__MODE__", mode).replace("__PREVIOUS__", "$true" if previous else "$false")
               .replace("__PYTHON__", python_path).replace("__BLOCK__", encoded_block))
    # No heredar credenciales, DATABASE_URL, tokens, PG* ni perfiles del proceso de trabajo.
    child_env = {name: value for name, value in os.environ.items()
                 if name.upper() in {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC"}}
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(program.encode("utf-16-le")).decode("ascii")],
        env=child_env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "stage3e_powershell_ok" in result.stdout
    assert "stage3e-fake-new-token" not in result.stdout + result.stderr
    assert "stage3e-fake-previous" not in result.stdout + result.stderr

"""Informe privado de HU-009. No es una cola ni evidencia de COMMIT comercial."""

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from uuid import uuid4


class DiscountQuarantineError(ValueError):
    """Error seguro: nunca publicar rutas, contenido o errores del sistema."""


_WINDOWS_ACL = r"""
$ErrorActionPreference = 'Stop'
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$path = $env:MITBOL_PRIVATE_REPORT_PATH
if ($env:MITBOL_PRIVATE_REPORT_SECURE -eq '1') {
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $path -AclObject $acl
}
$acl = Get-Acl -LiteralPath $path
if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { exit 3 }
# OWNER RIGHTS solo alcanza al propietario, ya comprobado como usuario actual.
$trusted = @($sid.Value, 'S-1-3-4', 'S-1-5-18', 'S-1-5-32-544')
$ownerAllowed = $false
foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
    $inheritOnly = ($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0
    if (-not $inheritOnly -and $rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted) { exit 3 }
    if (-not $inheritOnly -and $rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -in @($sid.Value, 'S-1-3-4')) { $ownerAllowed = $true }
}
if (-not $ownerAllowed) { exit 3 }
[Console]::Write('PRIVATE')
"""


def _windows_private(path: Path, *, secure_new_file=False):
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    env = {**os.environ, "MITBOL_PRIVATE_REPORT_PATH": str(path),
           "MITBOL_PRIVATE_REPORT_SECURE": "1" if secure_new_file else "0"}
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_ACL],
        env=env, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or result.stdout.strip() != "PRIVATE":
        raise DiscountQuarantineError("La cuarentena requiere permisos privados comprobables.")


def validate_quarantine_directory(value, *, isolated_test=False) -> Path:
    """El operador prepara una carpeta privada persistente. No cambia sus ACL."""
    try:
        path = Path(value)
        if not path.is_absolute() or not path.is_dir():
            raise DiscountQuarantineError("La cuarentena requiere una carpeta absoluta existente fuera de Git.")
        for component in (path, *path.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise DiscountQuarantineError("La cuarentena no admite enlaces ni puntos de redirección.")
            if (component / ".git").exists():
                raise DiscountQuarantineError("La cuarentena debe permanecer fuera de repositorios Git.")
        path = path.resolve(strict=True)
        temporary_roots = [Path(tempfile.gettempdir()).resolve(), Path("/var/tmp").resolve()]
        if os.getenv("RUNNER_TEMP"):
            temporary_roots.append(Path(os.environ["RUNNER_TEMP"]).resolve())
        ephemeral = any(os.getenv(key, "").lower() == "true" for key in ("GITHUB_ACTIONS", "RENDER"))
        if not isolated_test and (ephemeral or any(path.is_relative_to(root) for root in temporary_roots)):
            raise DiscountQuarantineError("Un destino temporal o un runner efímero no acredita retención privada persistente.")
        if os.name == "nt":
            _windows_private(path)
        else:
            info = path.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise DiscountQuarantineError("La carpeta de cuarentena requiere propietario actual y modo 0700.")
        return path
    except DiscountQuarantineError:
        raise
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise DiscountQuarantineError("No se pudo validar el destino privado de cuarentena.") from None


def _private_json(value):
    """Conserva decimales exactos y retira secretos incluso en campos adicionales."""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.search(
            r"token|password|passwd|secret|authorization|cookie|connection|database.?url|dsn|api.?key|access.?key",
            str(key), re.I) else _private_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_private_json(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DiscountQuarantineError("El informe contiene un decimal no finito.")
        return {"$decimal": str(value)}
    if isinstance(value, str) and re.search(
            r"postgres(?:ql)?(?:\+\w+)?://|(?:password|access_token|secret)\s*=", value, re.I):
        return "[REDACTED]"
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise DiscountQuarantineError("El informe contiene un dato no serializable de forma segura.")


def write_quarantine_report(directory, report: dict, *, isolated_test=False) -> str:
    """Publica un archivo nuevo con fsync y rename atómico, antes de cualquier DML.

    No sobrescribe informes anteriores. Un archivo 'prepared' no acredita que
    PostgreSQL haya confirmado la importación y no se actualiza tras el COMMIT.
    """
    temporary = None
    descriptor = None
    try:
        root = validate_quarantine_directory(directory, isolated_test=isolated_test)
        identifier = uuid4().hex
        final = root / f"discounts-{identifier}.json"
        payload = {**report, "report_id": identifier, "generated_at": datetime.now(timezone.utc).isoformat(),
            "commercial_commit": "not_attested_prepared_before_dml",
            "storage": "isolated_test_temporary" if isolated_test else "operator_managed_private_file",
            "external_backup_verified": False, "decimal_encoding": "tagged_exact_string"}
        serialized = json.dumps(_private_json(payload), ensure_ascii=True, allow_nan=False).encode("utf-8")
        descriptor, filename = tempfile.mkstemp(prefix=".discounts-", suffix=".tmp", dir=root)
        temporary = Path(filename)
        if os.name == "nt":
            _windows_private(temporary, secure_new_file=True)
        else:
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        if final.exists():
            raise DiscountQuarantineError("No se sobrescriben informes existentes.")
        os.replace(temporary, final)
        temporary = None
        if final.read_bytes() != serialized:
            raise DiscountQuarantineError("No se pudo verificar el informe privado publicado.")
        if os.name != "nt":
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return identifier
    except DiscountQuarantineError:
        raise
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise DiscountQuarantineError("No se pudo conservar el informe privado; no se autoriza la importación.") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

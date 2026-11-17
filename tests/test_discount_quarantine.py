"""Archivos privados locales; nunca lee credenciales ni usa servicios externos."""

from decimal import Decimal
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from app.services import discount_quarantine as quarantine


@pytest.fixture
def private_directory(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    return directory


def test_report_is_atomic_private_exact_and_explicitly_not_commit_evidence(private_directory):
    original = {"id": "synthetic-id", "discount_percent": Decimal("10.1234567890123456789")}
    identifier = quarantine.write_quarantine_report(private_directory,
        {"quarantined": [{"reason": "DISCOUNT_UNSUPPORTED_TYPE", "originals": [original]}]}, isolated_test=True)
    files = list(private_directory.iterdir())
    assert len(files) == 1 and files[0].name == f"discounts-{identifier}.json"
    report = json.loads(files[0].read_text())
    assert report["quarantined"][0]["originals"][0]["discount_percent"] == {"$decimal": str(original["discount_percent"])}
    assert report["commercial_commit"] == "not_attested_prepared_before_dml"
    assert report["storage"] == "isolated_test_temporary" and not report["external_backup_verified"]
    if os.name == "nt":
        quarantine._windows_private(files[0])
    else:
        assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


def test_report_never_contains_credentials_or_database_urls(private_directory):
    identifier = quarantine.write_quarantine_report(private_directory, dict(originals=[{
        "name": "Normal", "token": "PRIVATE_TOKEN", "nested": {"database_url": "postgresql://private"},
        "description": "postgresql+psycopg2://operator:PRIVATE_PASSWORD@example.invalid/database",
    }]), isolated_test=True)
    report = (private_directory / f"discounts-{identifier}.json").read_text()
    assert "PRIVATE_TOKEN" not in report and "PRIVATE_PASSWORD" not in report and "postgresql" not in report
    assert json.loads(report)["originals"][0]["name"] == "Normal"


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_io_failure_aborts_and_removes_only_own_staging_file(private_directory, monkeypatch, operation):
    backup = private_directory / "previous.json"
    backup.write_text("KEEP PREVIOUS")
    def fail(*args, **kwargs):
        raise OSError("PRIVATE_PATH PRIVATE_PASSWORD")
    monkeypatch.setattr(quarantine.os, operation, fail)
    with pytest.raises(quarantine.DiscountQuarantineError) as error:
        quarantine.write_quarantine_report(private_directory, {}, isolated_test=True)
    assert "PRIVATE" not in str(error.value)
    assert list(private_directory.iterdir()) == [backup] and backup.read_text() == "KEEP PREVIOUS"


def test_existing_report_is_never_overwritten(private_directory, monkeypatch):
    identifier = "a" * 32
    previous = private_directory / f"discounts-{identifier}.json"
    previous.write_text("ORIGINAL")
    monkeypatch.setattr(quarantine, "uuid4", lambda: SimpleNamespace(hex=identifier))
    with pytest.raises(quarantine.DiscountQuarantineError, match="sobrescriben"):
        quarantine.write_quarantine_report(private_directory, {}, isolated_test=True)
    assert previous.read_text() == "ORIGINAL" and len(list(private_directory.iterdir())) == 1


def test_repository_relative_and_nonexistent_destinations_are_rejected(private_directory):
    for value in ["relative", private_directory / "missing", Path(__file__).resolve().parents[1]]:
        with pytest.raises(quarantine.DiscountQuarantineError):
            quarantine.validate_quarantine_directory(value, isolated_test=True)


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_redirection_components_are_rejected_before_writing(private_directory, monkeypatch, kind):
    real = Path.lstat
    def metadata(path, *args, **kwargs):
        if path == private_directory:
            info = real(path, *args, **kwargs)
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else info.st_mode,
                                   st_file_attributes=0x400 if kind == "reparse" else 0)
        return real(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", metadata)
    with pytest.raises(quarantine.DiscountQuarantineError, match="redirección"):
        quarantine.write_quarantine_report(private_directory, {}, isolated_test=True)
    assert not list(private_directory.iterdir())


def test_temporary_or_ephemeral_output_is_not_accepted_as_persistent(private_directory, monkeypatch):
    with pytest.raises(quarantine.DiscountQuarantineError, match="persistente"):
        quarantine.validate_quarantine_directory(private_directory)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(quarantine.DiscountQuarantineError, match="persistente"):
        quarantine.write_quarantine_report(private_directory, {})
    assert not list(private_directory.iterdir())


def test_nonfinite_or_unserializable_values_cannot_publish_a_partial_report(private_directory):
    for value in [float("nan"), object()]:
        with pytest.raises(quarantine.DiscountQuarantineError):
            quarantine.write_quarantine_report(private_directory, {"original": value}, isolated_test=True)
    assert not list(private_directory.iterdir())

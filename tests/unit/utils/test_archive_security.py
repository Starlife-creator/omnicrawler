from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from omnicrawl.core.archive_security import UnsafePackageError, validate_zip_archive


def test_rejects_duplicate_case_insensitive_zip_paths(tmp_path: Path) -> None:
    package = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Models/item.bin", b"first")
        archive.writestr("models/ITEM.bin", b"second")

    with zipfile.ZipFile(package) as archive, pytest.raises(UnsafePackageError, match="duplicate"):
        validate_zip_archive(archive)


def test_rejects_suspicious_compression_ratio_before_reading_payload(tmp_path: Path) -> None:
    package = tmp_path / "compressed.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("component.json", b"{}")
        archive.writestr("payload.bin", b"0" * 1_000_000)

    with zipfile.ZipFile(package) as archive, pytest.raises(UnsafePackageError, match="compression ratio"):
        validate_zip_archive(archive, required=("component.json",))

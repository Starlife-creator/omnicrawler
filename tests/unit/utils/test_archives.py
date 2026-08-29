import io
import stat
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from omnicrawler.fetching.archives import (
    ArchiveLimits,
    UnsafeArchiveError,
)
from omnicrawler.fetching.archives import safe_extract_archive as _deprecated_safe_extract_archive


def safe_extract_archive(*args, **kwargs):
    """Exercise the compatibility implementation without leaking its expected warning."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="fetching.archives 已废弃.*",
            category=DeprecationWarning,
        )
        return _deprecated_safe_extract_archive(*args, **kwargs)


class ArchiveSafetyTest(unittest.TestCase):
    def test_legacy_entrypoint_declares_deprecation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "safe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("data.txt", "safe")
            with self.assertWarnsRegex(DeprecationWarning, "fetching.archives 已废弃"):
                _deprecated_safe_extract_archive(archive, root / "deprecated-output")

    def test_extracts_safe_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "safe.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                output.writestr("nested/data.txt", "safe content")
            files = safe_extract_archive(archive, root / "output")
            self.assertEqual((root / "output/nested/data.txt").read_text(), "safe content")
            self.assertEqual(files, [(root / "output/nested/data.txt").resolve()])

    def test_rejects_zip_traversal_and_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("ok.txt", "ok")
                output.writestr("../escape.txt", "bad")
            with self.assertRaises(UnsafeArchiveError):
                safe_extract_archive(archive, root / "output")
            self.assertFalse((root / "output").exists())
            self.assertFalse((root / "escape.txt").exists())

    def test_rejects_zip_symlink_and_high_ratio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "link.zip"
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(link, "target")
            with self.assertRaises(UnsafeArchiveError):
                safe_extract_archive(archive, root / "links")

            bomb = root / "ratio.zip"
            with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as output:
                output.writestr("zeros.bin", b"0" * 100_000)
            with self.assertRaises(UnsafeArchiveError):
                safe_extract_archive(
                    bomb, root / "ratio", limits=ArchiveLimits(max_compression_ratio=2)
                )

    def test_extracts_tar_and_rejects_tar_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "safe.tar"
            with tarfile.open(archive, "w") as output:
                info = tarfile.TarInfo("data/item.json")
                payload = b"{}"
                info.size = len(payload)
                output.addfile(info, io.BytesIO(payload))
            safe_extract_archive(archive, root / "tar-output")
            self.assertEqual((root / "tar-output/data/item.json").read_bytes(), b"{}")

            unsafe = root / "unsafe.tar"
            with tarfile.open(unsafe, "w") as output:
                link = tarfile.TarInfo("link")
                link.type = tarfile.SYMTYPE
                link.linkname = "outside"
                output.addfile(link)
            with self.assertRaises(UnsafeArchiveError):
                safe_extract_archive(unsafe, root / "unsafe-output")

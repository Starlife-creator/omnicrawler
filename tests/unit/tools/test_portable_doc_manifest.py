"""`tools/check_portable_doc_manifest.py` 便携包文档清单门禁反向断言（C-#15）。

反向断言：历史文档目录漏排除、必备根文件缺失、docs 整体漏拷都必须判红；
干净暂存目录必须通过。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_portable_doc_manifest_under_test", _ROOT / "tools" / "check_portable_doc_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MANIFEST = _load()


def _clean_release_root(tmp_path: Path) -> Path:
    root = tmp_path / "OmniCrawler"
    docs = root / "docs"
    docs.mkdir(parents=True)
    for name in MANIFEST.REQUIRED_ROOT_FILES:
        (root / name).write_text("ok", encoding="utf-8")
    (docs / "README.md").write_text("# 现行文档", encoding="utf-8")
    return root


def test_clean_release_root_passes(tmp_path: Path) -> None:
    root = _clean_release_root(tmp_path)
    assert MANIFEST.check_doc_manifest(root) == []


def test_missing_root_directory_reports(tmp_path: Path) -> None:
    issues = MANIFEST.check_doc_manifest(tmp_path / "absent")
    assert any("暂存目录不存在" in issue for issue in issues)


def test_forbidden_archive_directory_is_red(tmp_path: Path) -> None:
    root = _clean_release_root(tmp_path)
    (root / "docs" / "archive").mkdir()
    issues = MANIFEST.check_doc_manifest(root)
    assert any("docs/archive" in issue for issue in issues), issues


def test_forbidden_releases_directory_is_red(tmp_path: Path) -> None:
    root = _clean_release_root(tmp_path)
    (root / "docs" / "releases").mkdir()
    issues = MANIFEST.check_doc_manifest(root)
    assert any("docs/releases" in issue for issue in issues), issues


def test_missing_required_root_file_is_red(tmp_path: Path) -> None:
    root = _clean_release_root(tmp_path)
    (root / "LICENSE").unlink()
    issues = MANIFEST.check_doc_manifest(root)
    assert any("LICENSE" in issue for issue in issues), issues


def test_empty_docs_directory_is_red(tmp_path: Path) -> None:
    root = _clean_release_root(tmp_path)
    for md in root.glob("docs/*.md"):
        md.unlink()
    issues = MANIFEST.check_doc_manifest(root)
    assert any("整体漏拷" in issue for issue in issues), issues

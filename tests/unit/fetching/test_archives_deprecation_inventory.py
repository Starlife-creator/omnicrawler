"""A-P3-7：deprecated ``fetching.archives`` 残留 import 迁移清单回归测试。

生产源码除 ``__init__`` 惰性别名表外不得再直接引用本模块；模块本体保留过渡期兼容，
导入仍发 DeprecationWarning，不破坏既有测试调用方。
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "omnicrawler"

_IMPORT_PATTERNS = (
    re.compile(r"from\s+omnicrawler\.fetching\.archives\s+import"),
    re.compile(r"from\s+\.archives\s+import"),
    re.compile(r"import\s+omnicrawler\.fetching\.archives"),
    re.compile(r"from\s+fetching\.archives\s+import"),
)


def test_no_production_module_directly_imports_deprecated_archives() -> None:
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if path.name == "__init__.py":
            continue  # 惰性别名表保留向后兼容映射
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in _IMPORT_PATTERNS):
            offenders.append(str(path.relative_to(SRC_ROOT)))
    assert offenders == [], f"生产源码仍残留 deprecated archives 引用: {offenders}"


def test_deprecated_module_call_emits_warning_and_keeps_api(tmp_path: Path) -> None:
    import zipfile

    from omnicrawler.fetching.archives import ArchiveLimits, safe_extract_archive

    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("data.txt", "safe")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        files = safe_extract_archive(archive, tmp_path / "deprecated-output")

    deprecation = [item for item in caught if issubclass(item.category, DeprecationWarning)]
    assert deprecation, "过渡期函数调用必须继续发出 DeprecationWarning"
    assert "archive_security" in str(deprecation[0].message)
    assert files and (tmp_path / "deprecated-output/data.txt").read_text() == "safe"
    assert ArchiveLimits().max_entries > 0

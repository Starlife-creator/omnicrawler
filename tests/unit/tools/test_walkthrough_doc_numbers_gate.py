"""`docs/MANUAL_WALKTHROUGH.md` 的样本数字必须与 `EXPECTED` 一致（门禁自身的护栏）。

## 为什么需要这条

走查文档写着"样本固定：列表第 1 页 6 条、翻页后合计 8 条、附件 1 个"，
并声称这些数字由 `tools/walkthrough_demo_site.py` 的 `EXPECTED` 提供、
"不会与文档悄悄漂移"。但在此之前**没有任何门禁校验文档文字本身** ——
改一个样本常量（`LIST_TOTAL`），文档就会静默过期，而走查者会按过期数字
误判"翻页失败"或"漏采"，把环境问题报成产品缺陷。

本文件锁住的**不是数字**（数字由 `test_walkthrough_demo_site.py` 锁），
而是**门禁本身是否会失败**：数字被改坏要报、句子被删要报、
来源文件缺失也要报 —— 一个"匹配不到就静默通过"的门禁等于没有门禁。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_DOC = _ROOT / "docs" / "MANUAL_WALKTHROUGH.md"
_DEMO_SITE = _ROOT / "tools" / "walkthrough_demo_site.py"


def _gate():
    """按路径加载门禁模块（`tools/` 不是包，不能 import）。"""
    spec = importlib.util.spec_from_file_location(
        "_check_docs_consistency_under_test", _ROOT / "tools" / "check_docs_consistency.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fake_root(tmp_path: Path) -> Path:
    """一个只含所需两个文件的假仓库根。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "tools").mkdir()
    shutil.copy(_DEMO_SITE, tmp_path / "tools" / "walkthrough_demo_site.py")
    shutil.copy(_DOC, tmp_path / "docs" / "MANUAL_WALKTHROUGH.md")
    return tmp_path


def test_real_repo_doc_matches_expected() -> None:
    """当前仓库里文档数字与 EXPECTED 一致（改了样本常量就必须同步文档）。"""
    assert _gate().check_walkthrough_sample_numbers(_ROOT) == []


def test_wrong_number_is_reported(fake_root: Path) -> None:
    """文档数字被改坏 → 必须报错（且指出两边各是多少）。"""
    doc = fake_root / "docs" / "MANUAL_WALKTHROUGH.md"
    broken = doc.read_text(encoding="utf-8")
    broken = broken.replace("列表第 1 页 **6** 条、翻页后合计 **8** 条", "列表第 1 页 **7** 条、翻页后合计 **9** 条")
    broken = broken.replace("列表任务应含翻页后的合计 8 条", "列表任务应含翻页后的合计 9 条")
    doc.write_text(broken, encoding="utf-8")

    issues = _gate().check_walkthrough_sample_numbers(fake_root)
    assert issues, "数字与 EXPECTED 不符时必须报错"
    joined = " ".join(issues)
    assert "EXPECTED['list_items']=6" in joined, joined
    assert "EXPECTED[all_items]=8" in joined, joined


def test_removed_sentence_is_reported(fake_root: Path) -> None:
    """句子结构被改动 → 必须报错，不能"匹配不到就静默通过"。"""
    doc = fake_root / "docs" / "MANUAL_WALKTHROUGH.md"
    broken = doc.read_text(encoding="utf-8")
    broken = broken.replace("列表第 1 页 **6** 条", "列表第 1 页共 6 条")
    broken = broken.replace("列表任务应含翻页后的合计 8 条", "行数应符合预期")
    doc.write_text(broken, encoding="utf-8")

    issues = _gate().check_walkthrough_sample_numbers(fake_root)
    assert len(issues) == 2, issues
    assert all("找不到" in issue for issue in issues), issues


def test_missing_source_of_truth_is_reported(fake_root: Path) -> None:
    """来源文件缺失 → 报"无法校验"，而不是当作通过。"""
    (fake_root / "tools" / "walkthrough_demo_site.py").unlink()

    issues = _gate().check_walkthrough_sample_numbers(fake_root)
    assert issues and "无法校验" in issues[0], issues


def test_missing_doc_is_not_reported_here(fake_root: Path) -> None:
    """文档缺失由其它检查负责 —— 本检查不重复报（避免同一问题两条噪音）。"""
    (fake_root / "docs" / "MANUAL_WALKTHROUGH.md").unlink()

    assert _gate().check_walkthrough_sample_numbers(fake_root) == []

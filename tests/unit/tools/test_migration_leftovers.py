"""迁移遗留：区分「已移出」「刻意保留」「重复副本」。

## 这条守卫防的是什么

迁移完成后，宿主里仍能看到 "pdf" 字样。这**不一定是残留**——
有些是必须保留的入口层：

* `cli/_parsers/pdf.py`：`omnicrawler pdf` 的参数定义，删了用户没入口；
* `pipeline_ops/pdf_integration.py`：采集主流程挂接 PDF 阶段的编排点；
* `core/builtin_references.py`：`builtin:pdf/` 解析，是宿主的公开 API。

但"看起来像保留"和"真的是保留"是两回事。没有守卫的话，
两种情况都会发生：

1. 有人误删这些入口 → 用户功能消失；
2. 有人以为"还有 pdf 字样就是没迁干净" → 又开始拆，白做功。

## 判据

1. **已移出的包**不得回到宿主（`migrated_packages_absent()`）；
2. **刻意保留的入口文件**必须存在；
3. **重复副本**（`templates/pdf/`）的两份内容必须**逐字节一致**——
   一旦有人只改了一份，两份就会静默漂移。

详见 `MIGRATION_LEFTOVERS.md`。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from omnicrawler.services import document_facade

_REPO = Path(__file__).resolve().parents[3]
_PLUGIN = (
    _REPO.parent
    / "OmniCrawler-market"
    / "submissions"
    / "plugins"
    / "4c3014804d85de2568f85e62a3048429"
    / "pdf-workbench"
    / "omnicrawler_pdf"
)

#: 这些是**入口层**，必须保留在宿主（详见 MIGRATION_LEFTOVERS.md §二）。
_MUST_KEEP = [
    "src/omnicrawler/cli/_parsers/pdf.py",
    "src/omnicrawler/doc_extractors/pdf.py",
    "src/omnicrawler/pipeline_ops/pdf_integration.py",
    "src/omnicrawler/pipeline_ops/pdf_stage.py",
    "src/omnicrawler/pipeline_ops/pdf_region.py",
    "src/omnicrawler/services/pdf_quality_benchmark.py",
    "src/omnicrawler/core/builtin_references.py",
]

#: 两份副本必须一致的文件（宿主名 → 插件名相同）。
_SHARED_TEMPLATES = [
    "generic_template.yaml",
    "announcement_fields.yaml",
    "entity_master.csv",
]


def test_migrated_packages_are_gone_from_host() -> None:
    """四个包必须已从宿主移除（不可回退）。"""
    absent = document_facade.migrated_packages_absent()
    assert all(absent.values()), f"仍有包留在宿主: {absent}"


@pytest.mark.parametrize("relative", _MUST_KEEP)
def test_entry_layer_files_are_kept(relative: str) -> None:
    """入口层文件必须存在——删了等于砍掉用户入口或支柱①功能。"""
    path = _REPO / relative
    assert path.is_file(), (
        f"入口层文件缺失：{relative}。"
        "它看起来带 pdf 字样，但属于入口/编排/通用能力，不是文档工作台的实现。"
        "详见 MIGRATION_LEFTOVERS.md §二。"
    )


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
@pytest.mark.parametrize("name", _SHARED_TEMPLATES)
def test_shared_templates_are_identical(name: str) -> None:
    """宿主与插件的模板副本必须逐字节一致，否则会静默漂移。"""
    host = _REPO / "src" / "omnicrawler" / "templates" / "pdf" / name
    plugin = _PLUGIN / "templates" / "pdf" / name
    if not host.is_file() or not plugin.is_file():
        pytest.skip(f"副本不存在：host={host.is_file()} plugin={plugin.is_file()}")

    host_hash = hashlib.sha256(host.read_bytes()).hexdigest()
    plugin_hash = hashlib.sha256(plugin.read_bytes()).hexdigest()
    assert host_hash == plugin_hash, (
        f"{name} 两份副本不一致（host={host_hash[:12]} plugin={plugin_hash[:12]}）。"
        "只改一份会让两处行为开始漂移——要么同步，要么收敛成一份"
        "（见 MIGRATION_LEFTOVERS.md §三）。"
    )


def test_leftovers_doc_exists_and_lists_both_categories() -> None:
    """遗留清单必须同时写清「保留了什么」与「为什么」。"""
    doc = _REPO / "MIGRATION_LEFTOVERS.md"
    assert doc.is_file(), "缺少 MIGRATION_LEFTOVERS.md"
    text = doc.read_text(encoding="utf-8")
    for keyword in ("刻意保留", "技术债", "未完成"):
        assert keyword in text, f"清单缺少「{keyword}」一节"

"""市场三分支的搜索/过滤必须行为一致。

## 为什么

插件页早就有搜索与筛选（`_matches_filters`：查询 + 类型 + 模式 + 风险），
而模板页与组件页**没有**。同一个市场里三个标签页，一个能搜两个不能，
用户会以为另两个的条目不全。

## 判据

* 三支都能按关键词过滤；
* **两侧都归一大小写**（实测踩过：只归一了一侧，大写查询永远不匹配）；
* 匹配范围覆盖"用户会用来找东西"的字段：id / 名称 / 分类 / 标签 /
  发布者 / 上游域名；
* 空查询返回全部；
* 组件面板的搜索**不重新拉目录**（数据已在内存，只重画列表）。
"""

from __future__ import annotations

import pytest

from omnicrawler.gui.views.component_market_logic import (
    components_from_catalog,
    filter_components,
)
from omnicrawler.gui.views.template_market import _template_matches


def _component_catalog() -> dict:
    return {
        "components": [
            {
                "id": "tesseract-chi-sim",
                "version": "1.0.0",
                "name": "Tesseract 中文语言包",
                "publisher": "starlife",
                "recipe_digest": "a" * 64,
                "artifacts": [
                    {
                        "url": "https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata",
                        "sha256": "b" * 64,
                        "size": 1024,
                    }
                ],
            },
            {
                "id": "paddle-ocr-pack",
                "version": "2.0.0",
                "name": "PaddleOCR 模型",
                "publisher": "someone",
                "recipe_digest": "c" * 64,
                "artifacts": [
                    {
                        "url": "https://huggingface.co/x/y.bin",
                        "sha256": "d" * 64,
                        "size": 2048,
                    }
                ],
            },
        ]
    }


# ── 组件 ──────────────────────────────────────────────────────────────────
def test_component_filter_by_id_and_name() -> None:
    entries = components_from_catalog(_component_catalog())
    assert [e.id for e in filter_components(entries, "tesseract")] == ["tesseract-chi-sim"]
    assert [e.id for e in filter_components(entries, "PaddleOCR")] == ["paddle-ocr-pack"]


def test_component_filter_by_upstream_host() -> None:
    """能按上游域名找——用户常记得"我从 HF 装的"而不是组件名。"""
    entries = components_from_catalog(_component_catalog())
    assert [e.id for e in filter_components(entries, "huggingface")] == ["paddle-ocr-pack"]


def test_component_filter_is_case_insensitive() -> None:
    """大写查询也必须匹配（实测踩过只归一了一侧）。"""
    entries = components_from_catalog(_component_catalog())
    assert filter_components(entries, "TESSERACT"), "大写查询不应返回空"
    assert filter_components(entries, "GitHub"), "混合大小写不应返回空"


def test_component_filter_empty_query_returns_all() -> None:
    entries = components_from_catalog(_component_catalog())
    assert len(filter_components(entries, "")) == 2
    assert len(filter_components(entries, "   ")) == 2


def test_component_filter_no_match_returns_empty() -> None:
    entries = components_from_catalog(_component_catalog())
    assert filter_components(entries, "zzz-not-exist") == []


# ── 模板 ──────────────────────────────────────────────────────────────────
def test_template_filter_matches_id_name_category_tags() -> None:
    assert _template_matches({"id": "news", "name": "新闻聚合"}, "news")
    assert _template_matches({"id": "news", "name": "新闻聚合"}, "新闻")
    assert _template_matches({"id": "job", "category": "recruit"}, "recruit")
    assert _template_matches({"id": "x", "tags": ["招标"]}, "招标")
    assert not _template_matches({"id": "news", "name": "新闻聚合"}, "zzz")


def test_template_filter_is_case_insensitive() -> None:
    """与组件同一条纪律：两侧都归一。"""
    assert _template_matches({"id": "job", "category": "recruit"}, "RECRUIT")
    assert _template_matches({"id": "NEWS", "name": "新闻"}, "news")


# ── 面板 ──────────────────────────────────────────────────────────────────
pytest.importorskip("PySide6")


def test_component_panel_search_filters_without_refetch() -> None:
    """组件面板搜索只重画列表，不重新拉目录。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from omnicrawler.gui.views.component_manager import ComponentManagerPanel

    panel = ComponentManagerPanel(None, catalog_url="https://example.invalid/")
    try:
        # 直接注入已解析条目，避免联网
        from omnicrawler.gui.views.component_market_logic import parse_component_entry

        panel._entries = [
            parse_component_entry(item) for item in _component_catalog()["components"]
        ]
        panel._entries = [e for e in panel._entries if e is not None]

        panel._reload_catalog_filtered()
        assert panel._market_list.count() == 2

        panel._market_search.setText("tesseract")
        assert panel._market_list.count() == 1, "搜索未生效"

        panel._market_search.setText("zzz")
        assert panel._market_list.count() == 0

        panel._market_search.setText("")
        assert panel._market_list.count() == 2, "清空搜索后应恢复全部"
    finally:
        panel.deleteLater()

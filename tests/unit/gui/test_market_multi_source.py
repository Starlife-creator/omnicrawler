"""市场三栏（插件 / 模板 / 组件）必须共享同一套多源聚合语义。

## 为什么

`aggregate_catalogs` 早已实现逐源验签、失败可见、去重标冲突，但**只覆盖
plugins 与 templates 两类**——组件栏即使配了镜像源也永远看不到组件
（实测：聚合输出里根本没有 `components` 键）。

## 判据（与插件栏完全一致，不另造）

1. 聚合输出必须含三类：`plugins` / `templates` / `components`；
2. 每个条目带 `_package_type` / `_source` / `_trust` / `_kind`；
3. **逐源独立验签**：信任根为空 ⇒ 该源直接失败（fail-closed）；
4. **失败可见**：取数/验签失败的源照样出现在 `sources` 里并带 `error`；
5. **全部失败才算整体失败**：只要一条成功就要返回数据；
6. 本地离线目录作为最后兜底源（priority 9999）。

## 反向断言

把 `components` 从聚合的来源列表里删掉 ⇒ 组件栏守卫必红。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.gui.views.market_aggregation import (
    MarketAggregationError,
    collect_sources,
    failed_sources,
    source_summary,
)
from omnicrawler.plugins.market_client import aggregate_catalogs

_MARKET = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
_TRUST = _MARKET / "keys" / "plugin_trust.pub.pem"

pytestmark = pytest.mark.skipif(
    not (_MARKET / "catalog.json").is_file(),
    reason="需要 market 仓 catalog.json",
)


def _sources() -> list[dict]:
    return [
        {
            "url": str(_MARKET),
            "trust": str(_TRUST),
            "kind": "curated",
            "priority": 1,
            "enabled": True,
        }
    ]


def test_aggregate_covers_all_three_branches() -> None:
    """聚合输出必须同时含 plugins / templates / components。"""
    aggregated = aggregate_catalogs(_sources(), official_trust_source=str(_TRUST))

    for branch in ("plugins", "templates", "components"):
        assert branch in aggregated, (
            f"聚合输出缺 {branch} 分支 —— 该栏配了镜像源也看不到内容"
        )
        assert isinstance(aggregated[branch], list)


def test_component_entries_are_labelled() -> None:
    """组件条目必须带来源/信任/类型标记，供 UI 徽标。"""
    aggregated = aggregate_catalogs(_sources(), official_trust_source=str(_TRUST))
    components = aggregated.get("components") or []
    assert components, "聚合结果里没有组件 —— 市场仓至少有 2 个"

    for entry in components:
        assert entry.get("_package_type") == "component"
        assert entry.get("_source"), "缺少来源标记"
        assert entry.get("_trust"), "缺少信任级标记"
        assert entry.get("_kind"), "缺少源类型标记"


def test_plugin_and_template_still_labelled() -> None:
    """加组件时不能破坏既有的两类标记。"""
    aggregated = aggregate_catalogs(_sources(), official_trust_source=str(_TRUST))
    assert aggregated["plugins"], "插件条目消失了"
    assert aggregated["templates"], "模板条目消失了"
    assert all(e.get("_package_type") == "plugin" for e in aggregated["plugins"])
    assert all(e.get("_package_type") == "template" for e in aggregated["templates"])


def test_source_without_trust_fails_closed() -> None:
    """信任根为空 ⇒ 该源必须直接失败，不能"不验签就用"。"""
    aggregated = aggregate_catalogs(
        [{"url": str(_MARKET), "trust": "", "kind": "community", "priority": 1}],
        official_trust_source="",  # 也不给官方信任根
    )
    reports = aggregated.get("sources") or []
    assert reports, "应有源报告"
    assert not any(item.get("ok") for item in reports), (
        "信任根为空却报成功 —— fail-closed 失效"
    )
    assert any("信任根" in str(item.get("error") or "") for item in reports)


def test_failed_source_is_visible_not_dropped() -> None:
    """失败的源必须出现在报告里，不能静默丢弃。"""
    missing = _MARKET / "no-such-catalog-dir"
    aggregated = aggregate_catalogs(
        [
            {"url": str(_MARKET), "trust": str(_TRUST), "kind": "curated", "priority": 1},
            {"url": str(missing), "trust": str(_TRUST), "kind": "community", "priority": 2},
        ],
        official_trust_source=str(_TRUST),
    )
    reports = aggregated.get("sources") or []
    assert len(reports) == 2, "两个源都应出现在报告里"
    failed = failed_sources(aggregated)
    assert len(failed) == 1, f"应有一个失败源，实际 {len(failed)}"
    assert failed[0].get("error"), "失败源必须带 error 说明"


def test_disabled_source_is_reported() -> None:
    """禁用的源也要出现在报告里（带"已禁用"原因）。"""
    aggregated = aggregate_catalogs(
        [{"url": str(_MARKET), "trust": str(_TRUST), "kind": "curated", "enabled": False}],
        official_trust_source=str(_TRUST),
    )
    reports = aggregated.get("sources") or []
    assert reports and "禁用" in str(reports[0].get("error") or "")


def test_source_summary_renders_state() -> None:
    """源状态摘要要能区分"全部可用"与"部分失败"。"""
    all_ok = {"sources": [{"ok": True}, {"ok": True}]}
    assert source_summary(all_ok) == "2 个市场源可用"

    partial = {"sources": [{"ok": True}, {"ok": False}]}
    assert "1 个失败" in source_summary(partial)

    assert source_summary({"sources": []}) == ""


def test_collect_sources_appends_local_fallback() -> None:
    """本地目录必须作为最后一条兜底源，priority 最大（最后选用）。"""

    class _Cfg:
        def plugin_catalogs(self) -> list[dict]:
            return [{"url": "https://example.invalid/", "trust": "x", "kind": "community"}]

    sources = collect_sources(_Cfg(), local_fallback=_MARKET, trust_source=str(_TRUST))
    assert len(sources) == 2
    assert sources[-1]["url"] == str(_MARKET)
    assert sources[-1]["priority"] >= max(s.get("priority", 0) for s in sources[:-1])


def test_collect_sources_without_config() -> None:
    """无配置对象时返回空列表（调用方据此判空，而不是崩）。"""
    assert collect_sources(None) == []


def test_aggregate_raises_when_all_sources_fail() -> None:
    """全部失败才抛错——部分失败应返回数据 + 提示。"""

    class _Cfg:
        def plugin_catalogs(self) -> list[dict]:
            return [{"url": str(_MARKET / "missing-1"), "trust": str(_TRUST), "kind": "community"}]

    from omnicrawler.gui.views.market_aggregation import aggregate_market_catalogs

    with pytest.raises(MarketAggregationError):
        aggregate_market_catalogs(_Cfg())


def test_component_count_matches_catalog() -> None:
    """聚合出的组件数应与 catalog 里的一致（防止合并时漏项）。"""
    catalog = json.loads((_MARKET / "catalog.json").read_text(encoding="utf-8"))
    expected = len(catalog.get("components") or [])
    aggregated = aggregate_catalogs(_sources(), official_trust_source=str(_TRUST))
    assert len(aggregated.get("components") or []) == expected, (
        f"catalog 有 {expected} 个组件，聚合后只剩 "
        f"{len(aggregated.get('components') or [])} 个"
    )

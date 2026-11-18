"""市场多源聚合的**共享入口**：插件 / 模板 / 组件三栏共用同一套语义。

## 为什么需要这一层

市场下辖三栏。插件栏已用 `aggregate_catalogs` 做多源聚合（逐源独立验签、
失败可见、去重标冲突），而模板栏与组件栏仍只读**单源**。

如果三处各写一遍「组装 sources → 调用 → 处理全失败」，很快就会出现
三份略有差异的实现——其中一份漏了 fail-closed 或漏了"失败源要可见"，
就是安全缺口。

所以把这段收进一个函数，三栏都调它。

## 语义（与插件栏完全一致，不另造）

1. **逐源独立验签**：每个源用它自己的信任根；信任根为空 ⇒ 该源直接失败
   （fail-closed：宁可不显示，也不"不验签就用"）；
2. **失败可见**：取数/验签失败的源照样出现在 `sources` 报告里并带 `error`，
   绝不静默丢弃——否则用户以为"就这么多"；
3. **全部失败才算整体失败**：抛 `MarketAggregationError`，让视图进离线态；
   只要有一条成功，其余失败只作为提示；
4. **本地离线目录作为最后兜底源**（priority 9999）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class MarketAggregationError(RuntimeError):
    """所有市场源都不可用。

    **只在全部源都失败时抛**——只要有一条成功，调用方应展示数据并提示
    部分源不可用，而不是让整页空白。
    """


def collect_sources(
    config: Any,
    *,
    local_fallback: Path | None = None,
    trust_source: str = "",
) -> list[dict[str, Any]]:
    """从配置读出多源列表，并在末尾追加本地离线目录兜底。

    :param config: `AppConfig` 或任何提供 `plugin_catalogs()` 的对象。
    :param local_fallback: 本地市场仓目录；存在时作为最后一条兜底源。
    :param trust_source: 本地兜底源使用的信任根。
    """
    sources: list[dict[str, Any]] = []
    getter = getattr(config, "plugin_catalogs", None)
    if callable(getter):
        try:
            sources = [dict(item) for item in (getter() or [])]
        except Exception:  # noqa: BLE001 - 配置不可用按"无源"处理，由调用方判空
            sources = []
    if local_fallback is not None and Path(local_fallback).is_dir():
        sources.append(
            {
                "url": str(local_fallback),
                "trust": trust_source,
                "kind": "curated",
                "priority": 9999,
                "enabled": True,
            }
        )
    return sources


def aggregate_market_catalogs(
    config: Any,
    *,
    trust_source: str = "",
    local_fallback: Path | None = None,
    cache_root: str | Path | None = None,
    egress: Any = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """拉取并聚合全部市场源，返回聚合结果。

    抛 `MarketAggregationError` 当且仅当**所有源都失败**（含"没有任何源"）。
    """
    from ...plugins.market_client import aggregate_catalogs

    sources = collect_sources(
        config, local_fallback=local_fallback, trust_source=trust_source
    )
    if not sources:
        raise MarketAggregationError("没有配置任何市场源")

    aggregated = aggregate_catalogs(
        sources,
        official_trust_source=trust_source,
        cache_root=cache_root,
        egress=egress,
        timeout=timeout,
    )
    reports = aggregated.get("sources") or []
    if not any(item.get("ok") for item in reports):
        failures = "; ".join(
            f"{item.get('url')}: {item.get('error')}" for item in reports
        )
        raise MarketAggregationError(f"全部市场源都不可用：{failures}")
    return aggregated


def failed_sources(aggregated: Mapping[str, Any]) -> list[dict[str, Any]]:
    """聚合结果里失败的源（供 UI 显示"部分源不可用"提示）。"""
    return [
        dict(item)
        for item in (aggregated.get("sources") or [])
        if not item.get("ok")
    ]


def source_summary(aggregated: Mapping[str, Any]) -> str:
    """把源状态渲染成一行提示：`3 个源可用，1 个失败`。

    空字符串表示没有源（此时调用方应已判定整体失败）。
    """
    reports: Sequence[Mapping[str, Any]] = aggregated.get("sources") or []
    if not reports:
        return ""
    ok = sum(1 for item in reports if item.get("ok"))
    failed = len(reports) - ok
    if failed == 0:
        return f"{ok} 个市场源可用"
    return f"{ok} 个市场源可用，{failed} 个失败"

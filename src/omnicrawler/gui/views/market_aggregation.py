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

    `sources` 保留各源的失败报告，调用方据此判断"该不该建议换镜像"
    （安全类失败绝不建议换源，见 `services/mirror_failure_policy.py`）。
    """

    def __init__(self, message: str, *, sources: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.sources = list(sources or [])


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
        raise MarketAggregationError(
            f"全部市场源都不可用：{failures}", sources=list(reports)
        )
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

#: 索引滞后多久算"需要注意"（秒）。镜像同步周期通常数分钟到数小时；
#: 超过 24 小时基本可以确定对方没在同步了。
STALE_AFTER_SECONDS = 24 * 3600

#: 滞后多久开始温和提示（秒）。12 小时以上就值得标黄。
AGING_AFTER_SECONDS = 12 * 3600


def parse_generated_at(value: Any) -> float:
    """把 catalog 的 `generated_at` 解析成 epoch 秒；无法解析返回 0。"""
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        from datetime import datetime

        # catalog 用 ISO 8601 带时区（如 2026-11-18T16:28:59+00:00）
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except (ValueError, TypeError):
        return 0.0


def index_freshness(
    generated_at: Any,
    *,
    now: float | None = None,
) -> tuple[str, str]:
    """判断某个源的索引新鲜度，返回 (状态, 人类可读说明)。

    状态取值：

    * `fresh` —— 24 小时内，正常
    * `aging` —— 12~24 小时，温和提示
    * `stale` —— 超过 24 小时，明确提示
    * `unknown` —— 无法解析时间戳（旧格式或缺失）

    **为什么需要它**：镜像站有同步周期，可能拉不到刚发布的版本，或晚一步
    收到下架墓碑。用户看到"镜像里没有这个插件"时，需要知道是"真没有"
    还是"镜像还没同步"。
    """
    epoch = parse_generated_at(generated_at)
    if epoch <= 0:
        return "unknown", "索引时间未知"

    import time as _time

    reference = now if now is not None else _time.time()
    age = reference - epoch
    if age < 0:
        # 时间在未来：时钟偏差或恶意的旧签名，按未知处理而不是"非常新鲜"
        return "unknown", "索引时间异常"

    hours = age / 3600
    if age <= AGING_AFTER_SECONDS:
        return "fresh", f"索引生成于 {hours:.0f} 小时前" if hours >= 1 else "索引刚刚更新"
    if age <= STALE_AFTER_SECONDS:
        return "aging", f"索引已 {hours:.0f} 小时未更新，可能缺少最新内容"
    days = age / 86400
    return "stale", f"索引已 {days:.0f} 天未更新，可能缺少最新内容"


def freshness_of_sources(
    aggregated: Mapping[str, Any],
    *,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """对聚合结果里每个**成功**的源算新鲜度。

    失败源不参与——它们已经有 `error` 说明，再算新鲜度只会分散注意力。
    """
    out: list[dict[str, Any]] = []
    for item in aggregated.get("sources") or []:
        if not item.get("ok"):
            continue
        state, note = index_freshness(item.get("generated_at"), now=now)
        out.append(
            {
                "url": item.get("url", ""),
                "kind": item.get("kind", ""),
                "state": state,
                "note": note,
                "generated_at": item.get("generated_at", ""),
                "count": item.get("count", 0),
            }
        )
    return out


def freshness_summary(
    aggregated: Mapping[str, Any],
    *,
    now: float | None = None,
) -> str:
    """把各源新鲜度渲染成一行；全新鲜且单源时返回空串（不打扰）。"""
    states = freshness_of_sources(aggregated, now=now)
    if not states:
        return ""
    aging = [s for s in states if s["state"] in {"aging", "stale"}]
    if not aging:
        return ""
    parts = [f"{s['url']}: {s['note']}" for s in aging]
    return "；".join(parts)

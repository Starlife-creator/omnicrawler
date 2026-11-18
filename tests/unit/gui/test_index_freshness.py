"""索引新鲜度：让用户知道"镜像还没同步"而不是"市场里没有"。

## 为什么需要

镜像站有同步周期（数分钟至数小时）。用户看到"镜像里没有这个插件"时，
需要能区分两种情况：

* **真的没有**（该版本不存在）
* **镜像还没同步**（官方已有，镜像滞后）

没有这个信息，用户只会得出错误结论，甚至去官方源重试而不知为何"两边不一样"。

另外镜像**晚一步收到下架墓碑**（tombstone）意味着：用户可能从一个滞后的
镜像装到已被吊销的版本。所以滞后不只是体验问题。

## 判据

| 距 `generated_at` | 状态 | 用户可见 |
|---|---|---|
| ≤ 12 小时 | `fresh` | 不提示（正常路径不打扰） |
| 12~24 小时 | `aging` | 温和提示"可能缺少最新内容" |
| > 24 小时 | `stale` | 明确提示已多少天未更新 |
| 无法解析 | `unknown` | 说明"索引时间未知"，**不假装新鲜** |
| 时间在未来 | `unknown` | "索引时间异常"——不当作"非常新鲜" |
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from omnicrawler.gui.views.market_aggregation import (
    AGING_AFTER_SECONDS,
    STALE_AFTER_SECONDS,
    freshness_of_sources,
    freshness_summary,
    index_freshness,
    parse_generated_at,
)


def _iso(hours_ago: float, now: float) -> str:
    return datetime.fromtimestamp(now - hours_ago * 3600, UTC).isoformat()


_NOW = 1_800_000_000.0  # 固定基准，避免依赖真实时钟


def test_recent_index_is_fresh_and_silent() -> None:
    state, note = index_freshness(_iso(1, _NOW), now=_NOW)
    assert state == "fresh"
    assert "小时" in note or "刚刚" in note


def test_twelve_to_twenty_four_hours_is_aging() -> None:
    state, note = index_freshness(_iso(13, _NOW), now=_NOW)
    assert state == "aging", f"13 小时应判 aging，实际 {state}"
    assert "未更新" in note


def test_beyond_one_day_is_stale() -> None:
    state, note = index_freshness(_iso(50, _NOW), now=_NOW)
    assert state == "stale"
    assert "天" in note


def test_boundary_just_under_aging_threshold() -> None:
    """阈值边界：略小于 12 小时仍是 fresh。"""
    state, _ = index_freshness(_iso(11.9, _NOW), now=_NOW)
    assert state == "fresh"


def test_boundary_just_over_aging_threshold() -> None:
    """阈值边界：略超 12 小时开始 aging。"""
    state, _ = index_freshness(_iso(12.1, _NOW), now=_NOW)
    assert state == "aging"


def test_missing_timestamp_is_unknown_not_fresh() -> None:
    """缺时间戳必须判 unknown，**不能假装新鲜**。"""
    for value in ("", None, "   "):
        state, note = index_freshness(value, now=_NOW)
        assert state == "unknown", f"{value!r} 被判成 {state}"
        assert "未知" in note


def test_unparseable_timestamp_is_unknown() -> None:
    state, _ = index_freshness("not-a-date", now=_NOW)
    assert state == "unknown"


def test_future_timestamp_is_unknown_not_fresh() -> None:
    """时间在未来（时钟偏差或旧签名重放）按异常处理。

    若判成"非常新鲜"，恰好掩盖了重放攻击的信号。
    """
    state, note = index_freshness(_iso(-2, _NOW), now=_NOW)
    assert state == "unknown", f"未来时间被判成 {state}，应视为异常"
    assert "异常" in note


def test_parse_handles_z_suffix() -> None:
    """`Z` 后缀与 `+00:00` 必须等价。"""
    with_z = parse_generated_at("2026-11-18T16:28:59Z")
    with_offset = parse_generated_at("2026-11-18T16:28:59+00:00")
    assert with_z > 0
    assert with_z == with_offset


def test_parse_returns_zero_for_garbage() -> None:
    assert parse_generated_at("garbage") == 0.0
    assert parse_generated_at("") == 0.0
    assert parse_generated_at(None) == 0.0


def test_freshness_of_sources_skips_failed() -> None:
    """失败源不参与新鲜度——它们已有 error 说明。"""
    aggregated = {
        "sources": [
            {"url": "a", "ok": True, "generated_at": _iso(1, _NOW), "count": 3},
            {"url": "b", "ok": False, "error": "boom", "generated_at": ""},
        ]
    }
    states = freshness_of_sources(aggregated, now=_NOW)
    assert [s["url"] for s in states] == ["a"], "失败源不该出现在新鲜度列表里"


def test_summary_silent_when_all_fresh() -> None:
    """全新鲜时不打扰用户。"""
    aggregated = {"sources": [{"url": "a", "ok": True, "generated_at": _iso(1, _NOW)}]}
    assert freshness_summary(aggregated, now=_NOW) == ""


def test_summary_mentions_stale_source() -> None:
    aggregated = {
        "sources": [
            {"url": "https://mirror.example/", "ok": True, "generated_at": _iso(72, _NOW)}
        ]
    }
    text = freshness_summary(aggregated, now=_NOW)
    assert "mirror.example" in text
    assert "天" in text


def test_summary_empty_without_sources() -> None:
    assert freshness_summary({"sources": []}, now=_NOW) == ""
    assert freshness_summary({}, now=_NOW) == ""


def test_thresholds_are_ordered() -> None:
    """阈值必须自洽：aging 阈值小于 stale 阈值。"""
    assert AGING_AFTER_SECONDS < STALE_AFTER_SECONDS


@pytest.mark.parametrize(
    "hours,expected",
    [
        # 用 0.01 小时（36 秒）而不是 0：`hours=0` 会让生成的时间戳与 `now`
        # 完全相同，浮点往返后 age 可能变成极小的负数，触发"未来时间"分支
        # 而判成 unknown（实测在与其它测试同跑时复现）。这不是产品缺陷，
        # 是测试选了一个不该测的边界。
        (0.01, "fresh"),
        (6, "fresh"),
        (18, "aging"),
        (30, "stale"),
    ],
)
def test_state_matrix(hours: float, expected: str) -> None:
    now = time.time()
    state, _ = index_freshness(_iso(hours, now), now=now)
    assert state == expected

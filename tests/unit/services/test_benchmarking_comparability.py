"""基线可比性：四维对齐才下退化结论（W6.6 / §5.2 #8 的前置）。

## 为什么

`compare_benchmark` 原先只看吞吐比值 —— **跨档位、跨环境、输入已变**的两次运行
也会被据以报「性能退化」。那不只是噪声：它会让"退化告警"变成可忽略的东西。

W6.6 把「场景 / 输入快照 / 有效配置 / 依赖环境」四维写进记录，并要求：
**缺维度或维度不同 ⇒ 判为不可比、明确报出、不下退化结论**（而不是照旧拿平均值）。

本文件锁住这些语义，含"旧记录没有 `input_sha256` ⇒ 不可比"这条（方案明文要求）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from omnicrawler.services.benchmarking import (
    BenchmarkResult,
    _stored_snapshot,
    comparability,
    compare_benchmark,
)


def _result(**overrides) -> BenchmarkResult:
    base = {
        "profile": "standard",
        "pages": 10,
        "duration_seconds": 10.0,
        "peak_memory_bytes": 100_000_000,
        "bytes_transferred": 5_000_000,
        "errors": 0,
        "ok": True,
        "status": "succeeded",
        "config_sha256": "cfg",
        "effective_config_sha256": "eff",
        "profile_settings": (("concurrency", "3"), ("delay", "1.0")),
        "environment": (("python", "3.12.10"), ("platform", "linux")),
        "input_sha256": "input-a",
    }
    base.update(overrides)
    return BenchmarkResult(**base)  # type: ignore[arg-type]


# ── 可比性：四种维度各自都要能挡住 ────────────────────────────────────────


def test_identical_dimensions_are_comparable() -> None:
    comparable, reasons = comparability(_result(), _result())
    assert comparable is True
    assert reasons == []


def test_different_profile_is_incomparable() -> None:
    comparable, reasons = comparability(_result(), _result(profile="high"))
    assert comparable is False
    assert any("场景" in item and "档位" in item for item in reasons), reasons


def test_changed_profile_settings_is_incomparable() -> None:
    """同一档位的参数被改过 ⇒ 场景已变（档位定义负载，参数就是负载）。"""
    changed = _result(profile_settings=(("concurrency", "8"),))
    comparable, reasons = comparability(_result(), changed)
    assert comparable is False
    assert any("参数" in item and "改过" in item for item in reasons), reasons


def test_different_input_snapshot_is_incomparable() -> None:
    """输入内容变了 ⇒ 吞吐的差异可能来自站点本身，不是我们的代码。"""
    comparable, reasons = comparability(_result(), _result(input_sha256="input-b"))
    assert comparable is False
    assert any("输入快照" in item for item in reasons), reasons


def test_old_record_without_snapshot_is_incomparable() -> None:
    """**旧记录**（没有 `input_sha256` 等维度）必须判为不可比，而不是照平均值。"""
    comparable, reasons = comparability(_result(input_sha256=""), _result())
    assert comparable is False
    assert any("输入快照缺失" in item for item in reasons), reasons


def test_different_effective_config_is_incomparable() -> None:
    comparable, reasons = comparability(_result(), _result(effective_config_sha256="other"))
    assert comparable is False
    assert any("有效配置" in item for item in reasons), reasons


def test_different_environment_is_incomparable() -> None:
    comparable, reasons = comparability(
        _result(), _result(environment=(("python", "3.13.12"), ("platform", "linux")))
    )
    assert comparable is False
    assert any("依赖环境" in item for item in reasons), reasons


def test_environment_order_does_not_matter() -> None:
    """环境是键值集合：元组顺序不同不该被当成"不同环境"（否则会误报不可比）。"""
    swapped = _result(environment=(("platform", "linux"), ("python", "3.12.10")))
    comparable, reasons = comparability(_result(), swapped)
    assert comparable is True, reasons


# ── compare_benchmark：不可比时不下结论 ──────────────────────────────────


def test_regression_is_reported_only_when_comparable() -> None:
    slow = _result(duration_seconds=20.0)  # 吞吐减半
    check = compare_benchmark(_result(), slow, regression_threshold=0.1)
    assert check["comparable"] is True
    assert check["regression"] is True
    assert check["incomparable_reasons"] == []


def test_incomparable_pair_never_reports_regression() -> None:
    """即使吞吐确实掉了一半，只要不可比就**不报退化**，但要说明为什么。"""
    slow_other_profile = _result(profile="high", duration_seconds=20.0)
    check = compare_benchmark(_result(), slow_other_profile, regression_threshold=0.1)
    assert check["comparable"] is False
    assert check["regression"] is False
    assert check["incomparable_reasons"], check
    # 比值仍作为信息保留（便于人工判断），但不会被当成结论
    assert float(str(check["throughput_change"])) < 0


# ── 输入快照本身 ────────────────────────────────────────────────────────


def _state_db(workspace: Path, rows: list[tuple[str, str, str, int]]) -> None:
    db = workspace / "state.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS responses "
        "(run_id TEXT, url TEXT, content_sha256 TEXT, size_bytes INTEGER)"
    )
    conn.execute("DELETE FROM responses")  # 允许同一目录复用（本文件里会重建两次）
    conn.executemany("INSERT INTO responses VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_snapshot_is_empty_without_state_database(tmp_path: Path) -> None:
    assert _stored_snapshot(tmp_path, "run-1") == ""
    assert _stored_snapshot(None, "run-1") == ""
    assert _stored_snapshot(tmp_path, "") == ""


def test_snapshot_tracks_content_not_just_size(tmp_path: Path) -> None:
    """同一字节数、不同内容 ⇒ 快照必须不同（否则输入变了也看不出来）。"""
    _state_db(tmp_path, [("run-1", "https://a/", "sha-a", 100), ("run-1", "https://b/", "sha-b", 100)])
    first = _stored_snapshot(tmp_path, "run-1")

    _state_db(tmp_path, [("run-2", "https://a/", "sha-a", 100), ("run-2", "https://b/", "sha-c", 100)])
    second = _stored_snapshot(tmp_path, "run-2")

    assert first and second
    assert first != second, "内容不同（字节数相同）却没有区分开"


def test_snapshot_is_stable_for_the_same_run(tmp_path: Path) -> None:
    rows = [("run-1", "https://b/", "sha-b", 100), ("run-1", "https://a/", "sha-a", 200)]
    _state_db(tmp_path, rows)
    assert _stored_snapshot(tmp_path, "run-1") == _stored_snapshot(tmp_path, "run-1")


def test_run_without_responses_has_no_snapshot(tmp_path: Path) -> None:
    _state_db(tmp_path, [("other-run", "https://a/", "sha-a", 100)])
    assert _stored_snapshot(tmp_path, "run-1") == ""

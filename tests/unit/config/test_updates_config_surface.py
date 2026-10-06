"""`updates` 段的**配置面**事实：删掉的键必须真的消失，且既有配置只收到**警告**。

背景（2026-09-30 用户拍板）：`updates.confirm_missing_runs` 从 v1.1.0（初始历史）起就
**只有默认值与一条校验、零消费点** —— 连它引用的测试文件都全仓不存在，即
"删除需连续 N 次确认"这条不变量**从未实现**。它已删除。

★ 为什么两条断言都要有（只留一条会漏掉一半风险）：
  ① 只断言"键没了" ⇒ 不会发现**既有配置被打红**（那会把无害的遗留键升级成"打不开的工具"）；
  ② 只断言"只警告" ⇒ 不会发现**键其实还在**（那开关会继续误导人）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.config import DEFAULTS, load_config, validate_config


@pytest.mark.parametrize("policy", [
    {"confirmations": True}, {"confirmations": 0}, {"cooldown_seconds": -1},
    {"fields": {"price": {"minimum_absolute_change": True}}},
    {"fields": {"price": {"minimum_relative_change": float("nan")}}},
    {"fields": {"price": {"direction": []}}}, {"unknown": True},
])
def test_invalid_record_notification_policy_is_rejected(tmp_path, policy):
    from omnicrawler.core.config import AppConfig, deep_merge

    raw = deep_merge(DEFAULTS, {"source": {"seeds": ["https://example.test"]},
                              "updates": {"notifications": {"policy": policy}}})
    config = AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")
    errors, _ = validate_config(config)
    assert any("通知" in error for error in errors)


def test_confirm_missing_runs_is_no_longer_a_default_key() -> None:
    """① 该键必须真的从默认配置面消失（否则用户仍会以为它在起作用）。"""
    assert "confirm_missing_runs" not in DEFAULTS["updates"]


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_legacy_confirm_missing_runs_warns_but_does_not_error(tmp_path: Path) -> None:
    """② 既有配置里仍有该键 ⇒ **警告**（告知式移除），**不是错误**（不能让人打不开工具）。"""
    path = _write(
        tmp_path,
        "project: {name: t, workspace: work}\n"
        "source: {kind: crawl, seeds: [https://example.com/]}\n"
        "updates: {enabled: true, confirm_missing_runs: 5}\n",
    )

    _errors, warnings = validate_config(load_config(path))

    hits = [w for w in warnings if "confirm_missing_runs" in w]
    assert hits, f"应给出一条未知字段警告，实际 warnings={warnings}"


def test_legacy_confirm_missing_runs_is_not_an_error_even_in_strict_mode(tmp_path: Path) -> None:
    """③ 严格模式下的边界：未知字段会进 errors —— 这是**既有**行为、不是本键特例。

    记录这条是为了把"删键的代价"说清：严格模式（`strict=True`，CI/维护者用）下，
    带该键的配置会报错。★ 这是刻意的——严格模式本来就要求"零未知字段"。
    """
    path = _write(
        tmp_path,
        "project: {name: t, workspace: work}\n"
        "source: {kind: crawl, seeds: [https://example.com/]}\n"
        "updates: {enabled: true, confirm_missing_runs: 5}\n",
    )

    errors, _warnings = validate_config(load_config(path), strict=True)

    assert any("confirm_missing_runs" in e for e in errors), errors

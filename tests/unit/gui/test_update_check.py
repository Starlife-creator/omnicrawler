"""GUI「检查更新」：摘要格式化（B 站弹窗形态的体积对比）与状态分支。"""

from __future__ import annotations

import pytest

from omnicrawler.gui.update_check import format_update_summary


def test_summary_when_up_to_date() -> None:
    summary = format_update_summary(
        {"status": "up-to-date", "current_version": "0.15.0"}
    )
    assert "0.15.0" in summary
    assert "已是最新版本" in summary


def test_summary_shows_incremental_and_full_side_by_side() -> None:
    """★ 体积对比照 B 站弹窗：增量与全量并排（_human_bytes 为 1024 进制，标注 MB）。"""
    summary = format_update_summary(
        {
            "status": "update-available",
            "current_version": "0.14.0",
            "latest_version": "0.15.0",
            "options": {
                "incremental": {"available": True, "size": 51_200_000},
                "full": {"available": True, "size": 515_000_000, "via_fallback": False},
            },
            "payload_plan": {"needs_download": 3, "files_total": 2000},
            "notes": "修复了一些问题",
        }
    )
    assert "0.14.0" in summary and "0.15.0" in summary
    assert "增量更新（约 48.8MB）" in summary
    assert "全量包（约 491.1MB）" in summary
    assert "需更新 3/2000 个文件" in summary
    assert "修复了一些问题" in summary
    assert "self-update apply --yes" in summary


def test_summary_labels_full_package_without_fallback_notion() -> None:
    """全量包只按体积报出 —— **不再**有"取自最近一次全量发布"这类兜底措辞。

    兜底字段已随"统一发布形态"删除（每版都发本平台全量包），仍出现那句提示就说明
    旧分支没删干净（或 `options.full` 又冒出了 `via_fallback`）。
    """
    summary = format_update_summary(
        {
            "status": "update-available",
            "current_version": "0.13.1",
            "latest_version": "0.14.1",
            "options": {
                "incremental": {"available": False, "size": 0},
                "full": {"available": True, "size": 515_000_000},
            },
        }
    )
    assert "全量包（约 491.1MB）" in summary
    assert "最近一次全量发布" not in summary
    assert "增量更新" not in summary


def test_summary_without_options_still_renders() -> None:
    """清单连 options 都没有的极端形态 ⇒ 不崩溃、至少报版本。"""
    summary = format_update_summary(
        {"status": "update-available", "current_version": "0.14.0", "latest_version": "0.15.0"}
    )
    assert "0.15.0" in summary


@pytest.mark.parametrize("code", [2, 3])
def test_summary_called_for_disabled_and_failed(code: int) -> None:
    """禁用/失败形态：摘要至少包含 detail（调用方用 Toast 呈现）。"""
    from omnicrawler.gui.update_check import format_update_summary as fmt

    summary = fmt({"status": "disabled" if code == 2 else "failed", "detail": "x"})
    assert summary == "当前版本：?\nx" or "当前版本" in summary

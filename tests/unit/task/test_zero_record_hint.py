"""走查 R2.1：0 记录提示必须**指向真因**，而不是列出与事实无关的三条。

背景（0.13.0 实测，2026-09-18 走查 R2.1）：旧实现是一段静态文案，无论实际发生了什么，
都打印「① 无数据 ② 出网被拦截 ③ 模板不匹配」。实测同一工作区复跑时真因是
「内容未变化，被增量去重跳过」—— 三条里一条都不沾边，用户被引向错误方向，
而真正的下一步是 `omnicrawler export` 从断点库重导。

本文件守住「判断依据来自本次运行事实」这一性质：三个典型场景分别断言到对应分支。
"""

from __future__ import annotations

from omnicrawler.commands.run_task import _zero_record_hint

_RECORDS_KEY = "records"


def test_blocked_without_processing_points_to_security_evidence() -> None:
    hint = _zero_record_hint({"frontier": {"blocked": 3}, "processed": 0, "records": 0})
    assert "有效记录为 0" in hint
    assert "安全策略或 robots" in hint
    assert "3" in hint, "应给出被拦数量"
    assert "security-report" in hint, "应指向可查拦截明细的命令"


def test_processed_but_no_records_explains_rerun_and_selector_cases() -> None:
    """★ 这是复跑场景的真实分支：必须先讲「0 条不代表失败」再讲选择器。"""
    hint = _zero_record_hint({"frontier": {}, "processed": 12, "records": 0})
    assert "已成功处理 12 个页面" in hint
    assert "增量去重跳过" in hint
    assert "不代表失败" in hint
    assert "export" in hint, "必须给出从断点库重导的下一步"
    assert "field-suggest" in hint, "同时给出首次运行时的排查路径"
    # 不得再把不相干的「无数据」当前提
    assert "目标网站当前无数据" not in hint


def test_nothing_processed_points_to_request_never_sent() -> None:
    hint = _zero_record_hint({"frontier": {"pending": 5}, "processed": 0, "records": 0})
    assert "请求根本没发出去" in hint
    assert "doctor" in hint
    assert "preflight" in hint


def test_error_count_is_appended_when_present() -> None:
    plain = _zero_record_hint({"frontier": {}, "processed": 3, "records": 0})
    with_errors = _zero_record_hint({"frontier": {}, "processed": 3, "records": 0, "errors": 4})
    assert "error_center.html" not in plain, "没有错误时不该提错误中心"
    assert "4 条错误" in with_errors
    assert "error_center.html" in with_errors


def test_missing_frontier_key_does_not_crash() -> None:
    """旧调用方可能不带 frontier（例如占位符门禁提前返回的那条路径）。"""
    hint = _zero_record_hint({"processed": 0, "records": 0})
    assert "有效记录为 0" in hint


def test_hint_still_carries_doctor_for_legacy_expectation() -> None:
    """既有用例（tests/unit/task/test_run_exit_semantics.py）断言提示含 doctor —— 保持兼容。"""
    assert "doctor" in _zero_record_hint({"frontier": {}, "processed": 3, _RECORDS_KEY: 0})

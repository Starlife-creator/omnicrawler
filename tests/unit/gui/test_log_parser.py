"""C-#9：``gui/runner/log_parser.py`` 纯逻辑单测（无 Qt 依赖）。

覆盖日志级别判定、旧式 PROGRESS 正则与回调、统计模式累积/重置、
PROGRESS2 JSON 事件还原与双回调兼容。Qt widget 层测试待 GUI 环境补。
"""

from __future__ import annotations

from omnicrawler.gui.runner.log_parser import LogParser

# ---- 级别判定 ---------------------------------------------------------------


def test_level_prefix_warning_is_warn() -> None:
    result = LogParser().parse_line("WARNING: 磁盘空间不足")
    assert result["level"] == "warn"


def test_level_prefix_error_is_error() -> None:
    result = LogParser().parse_line("ERROR: 采集阶段失败")
    assert result["level"] == "error"


def test_level_prefix_info_is_info() -> None:
    result = LogParser().parse_line("INFO: 任务启动")
    assert result["level"] == "info"


def test_level_substring_error_fallback() -> None:
    # 无显式前缀时按关键词子串判定；类型名中的 error 也归 error。
    result = LogParser().parse_line("PermissionError: 写入被拒绝")
    assert result["level"] == "error"


def test_level_plain_line_is_info() -> None:
    result = LogParser().parse_line("开始处理任务")
    assert result["level"] == "info"


# ---- 旧式 PROGRESS ----------------------------------------------------------


def test_old_style_progress_extracts_percent_and_url() -> None:
    result = LogParser().parse_line("PROGRESS: 45% https://example.com/page")
    assert result["progress"] == {"percent": 45, "url": "https://example.com/page"}


def test_old_style_progress_invokes_callback() -> None:
    seen: list[tuple[int, str]] = []
    parser = LogParser(on_progress=lambda percent, url: seen.append((percent, url)))
    parser.parse_line("PROGRESS: 72% https://example.com")
    assert seen == [(72, "https://example.com")]


# ---- 统计模式 ---------------------------------------------------------------


def test_stats_chinese_patterns_are_captured() -> None:
    parser = LogParser()
    parser.parse_line("采集页面: 3")
    parser.parse_line("提取记录: 45")
    parser.parse_line("下载附件: 2")
    assert parser.get_stats() == {"pages": 3, "records": 45, "downloaded": 2}


def test_stats_english_patterns_are_captured() -> None:
    result = LogParser().parse_line("crawled pages: 8 · extracted records: 12")
    assert result["stats"]["pages"] == 8
    assert result["stats"]["records"] == 12


def test_stats_accumulate_and_reset() -> None:
    parser = LogParser()
    parser.parse_line("采集页面: 3")
    parser.parse_line("采集页面: 5")
    assert parser.get_stats() == {"pages": 5}
    parser.reset_stats()
    assert parser.get_stats() == {}


# ---- PROGRESS2 事件 ---------------------------------------------------------


def test_progress2_line_restores_event_and_fires_callbacks() -> None:
    from omnicrawler.services.progress import TaskProgressEvent

    event = TaskProgressEvent(percent=63.0, message="处理中", extra={"url": "https://x.test"})
    percent_seen: list[int] = []
    event_seen: list[object] = []
    parser = LogParser(
        on_progress=lambda p, _url: percent_seen.append(p),
        on_progress_event=lambda ev: event_seen.append(ev),
    )
    result = parser.parse_line(event.to_log_line())

    assert result["progress_event"] is not None
    assert result["progress_event"].percent == 63.0
    assert percent_seen == [63]
    assert event_seen and event_seen[0].message == "处理中"


def test_progress2_invalid_json_yields_no_event() -> None:
    result = LogParser().parse_line("PROGRESS2: {not-json")
    assert result["progress_event"] is None

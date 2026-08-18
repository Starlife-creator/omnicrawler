"""S3.3.1：CLI 输出快照驱动测试——GUI LogParser 解析真实 CLI 输出。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.gui.runner.log_parser import LogParser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cli_outputs"


def _parse(fixture: str) -> tuple[LogParser, list[dict]]:
    parser = LogParser()
    results: list[dict] = []
    for line in (FIXTURES / fixture).read_text(encoding="utf-8").splitlines():
        results.append(parser.parse_line(line))
    return parser, results


def test_normal_run_snapshot() -> None:
    parser, results = _parse("normal_run.log")
    assert parser.get_stats() == {"pages": 3, "records": 45, "downloaded": 2}
    progress = [r["progress"] for r in results if r["progress"]]
    assert progress and progress[-1] == {"percent": 30, "url": "https://example.org/page3"}


def test_failed_run_snapshot() -> None:
    parser, results = _parse("failed_run.log")
    assert any(r["level"] == "error" for r in results)
    assert parser.get_stats() == {}


def test_zero_records_run_snapshot() -> None:
    parser, results = _parse("zero_records_run.log")
    assert parser.get_stats() == {}
    assert any(r["level"] == "warn" for r in results)


def test_exception_run_snapshot() -> None:
    parser, results = _parse("exception_run.log")
    assert any(r["level"] == "error" for r in results)
    # 异常类型真实出现在 CLI 输出（parse_line 不保留原始行，直接校验 fixture）
    assert "ExtractionError" in (FIXTURES / "exception_run.log").read_text(encoding="utf-8")


def test_blocked_network_snapshot() -> None:
    parser, results = _parse("blocked_network_run.log")
    assert any(r["level"] == "warn" for r in results)  # 拦截告警（WARNING 前缀权威）
    assert any(r["level"] == "error" for r in results)  # 抓取失败
    assert parser.get_stats() == {"records": 0}


def test_progress_callback_invoked() -> None:
    seen: list[tuple[int, str]] = []
    parser = LogParser(on_progress=lambda percent, url: seen.append((percent, url)))
    for line in (FIXTURES / "normal_run.log").read_text(encoding="utf-8").splitlines():
        parser.parse_line(line)
    assert seen == [(30, "https://example.org/page3")]

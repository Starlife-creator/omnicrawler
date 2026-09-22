"""本地审计日志的自测 —— 安装/卸载事件必须留痕可查（§十 P1）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.gui.views.plugin_market_logic import (
    _append_market_event,
    _read_market_events,
)


def test_roundtrip_append_and_read(tmp_path: Path) -> None:
    assert _append_market_event(tmp_path, "install", "demo", version="0.4.0") is True
    assert _append_market_event(tmp_path, "uninstall", "demo", version="0.4.0") is True
    events = _read_market_events(tmp_path)
    assert [e["action"] for e in events] == ["install", "uninstall"]
    assert all(e["plugin_id"] == "demo" for e in events)
    assert all(e["ts"] for e in events)


def test_missing_log_returns_empty(tmp_path: Path) -> None:
    assert _read_market_events(tmp_path) == []


def test_malformed_lines_are_skipped(tmp_path: Path) -> None:
    (tmp_path / ".market-events.jsonl").write_text(
        "{broken\n{\"action\": \"install\", \"plugin_id\": \"x\", \"ts\": \"t\"}\n\n",
        encoding="utf-8",
    )
    events = _read_market_events(tmp_path)
    assert len(events) == 1 and events[0]["plugin_id"] == "x"


def test_write_failure_returns_false(tmp_path: Path) -> None:
    """日志路径是目录 ⇒ 打开失败 ⇒ 返回 False（不抛异常、不阻断主流程）。"""
    (tmp_path / ".market-events.jsonl").mkdir()
    assert _append_market_event(tmp_path, "install", "demo") is False

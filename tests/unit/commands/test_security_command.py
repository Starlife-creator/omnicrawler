"""C-#7：``commands.security`` 审计报告命令直接单测（缺失文件空报告 + 事件汇总）。"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from omnicrawler.commands import security as security_command


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "sec-cmd", "workspace": str(tmp_path / "work")},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True},
        "http": {"user_agent": "SecCmd/1.0", "respect_robots": False, "delay_seconds": 0},
    }, sort_keys=False), encoding="utf-8")
    return path


def test_missing_audit_log_returns_empty_summary(tmp_path: Path) -> None:
    result = security_command.execute(str(_write_config(tmp_path)))
    assert result["events"] == {}
    assert result["invalid_lines"] == 0


def test_audit_log_events_are_summarized(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    log_dir = tmp_path / "work" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / "egress-audit.jsonl"
    log.write_text("\n".join([
        json.dumps({"event": "request", "purpose": "fetch", "subject": "example.com", "url": "https://example.com/a"}),
        json.dumps({"event": "request", "purpose": "fetch", "subject": "example.com", "url": "https://example.com/b"}),
        json.dumps({"event": "blocked", "purpose": "fetch", "subject": "other.com", "url": "https://other.com/"}),
        "{not-json",
    ]), encoding="utf-8")

    result = security_command.execute(str(config))
    assert result["events"]["request"] == 2
    assert result["events"]["blocked"] == 1
    assert result["invalid_lines"] == 1

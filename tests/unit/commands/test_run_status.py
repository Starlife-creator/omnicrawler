"""C-#7：``commands.run_status`` 状态分支与文本渲染直接单测。"""
from __future__ import annotations

from pathlib import Path

import yaml

from omnicrawler.commands import run_status


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "status-cmd", "workspace": str(tmp_path / "work")},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True},
        "http": {"user_agent": "StatusCmd/1.0", "respect_robots": False, "delay_seconds": 0},
    }, sort_keys=False), encoding="utf-8")
    return path


def test_fresh_workspace_reports_not_started(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    result = run_status.execute(str(path))
    assert result["crawl"]["status"] == "not_started"
    assert result["pdf"]["status"] == "not_configured"
    assert Path(result["config_path"]).name == "project.yaml"


def test_text_render_covers_status_totals_and_pdf(capsys) -> None:
    run_status._print_text({
        "crawl": {
            "latest_run": {"status": "succeeded", "started_at": "t0", "finished_at": "t1", "project_name": "p"},
            "totals": {"pending": 1, "done": 4, "failed": 1, "in_progress": 0, "records": 4, "artifacts": 2},
        },
        "pdf": {"status": "not_configured"},
        "config_path": "/tmp/project.yaml",
    })
    out = capsys.readouterr().out
    assert "采集状态" in out and "succeeded" in out
    assert "统计" in out and "已完成" in out and "失败" in out
    assert "提取记录" in out and "附件" in out
    assert "PDF 子项目" in out

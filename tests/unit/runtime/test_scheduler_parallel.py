"""S2.5.44：调度并行执行 + UTC 基准。"""

from __future__ import annotations

import time
from pathlib import Path

from omnicrawl.runtime.schedule_conditions import evaluate_conditions
from omnicrawl.runtime.scheduler import ScheduleStore


def test_run_due_runs_in_parallel(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("project: {name: x}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    with ScheduleStore(tmp_path / "sched.sqlite3") as schedules:
        schedules.add("a", config, 60, start_at=100)
        schedules.add("b", config, 60, start_at=100)
        schedules.add("c", config, 60, start_at=100)
        active: list[str] = []

        def _executor(_path: Path) -> str:
            active.append("x")
            time.sleep(0.2)  # 长任务——若串行则总耗时 ≥ 0.6s
            return "done"

        started = time.monotonic()
        results = schedules.run_due(_executor, limit=10)
        elapsed = time.monotonic() - started
        assert len(results) == 3
        assert elapsed < 0.6  # 并行：3 个 0.2s 任务远小于串行 0.6s
        assert all(item["ok"] for item in results)


def test_allowed_hours_uses_utc(tmp_path: None = None) -> None:
    from datetime import datetime, timezone

    utc_hour = datetime.now(timezone.utc).hour
    allowed, reason = evaluate_conditions({"allowed_hours": [utc_hour]})
    assert allowed is True
    blocked, _reason = evaluate_conditions({"allowed_hours": [(utc_hour + 1) % 24]})
    assert blocked is False

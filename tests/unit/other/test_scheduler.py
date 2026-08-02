from __future__ import annotations

from pathlib import Path

from omnicrawl.runtime.scheduler import ScheduleStore


def test_schedule_lease_recovery_and_next_run(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("project: {name: x}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    with ScheduleStore(tmp_path / "schedules.sqlite3") as schedules:
        schedule_id = schedules.add("demo", config, 60, start_at=100)
        first = schedules.claim_due(now=100, lease_seconds=30)
        assert [item["schedule_id"] for item in first] == [schedule_id]
        assert schedules.claim_due(now=110) == []
        recovered = schedules.claim_due(now=131)
        assert [item["schedule_id"] for item in recovered] == [schedule_id]
        schedules.finish(schedule_id, ok=True, now=140)
        saved = schedules.list()[0]
        assert saved["last_status"] == "succeeded"
        assert saved["next_run_at"] == 200

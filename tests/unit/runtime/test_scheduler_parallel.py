"""S2.5.44：调度并行执行 + UTC 基准。"""

from __future__ import annotations

import threading
import time
from datetime import UTC
from pathlib import Path

from omnicrawler.runtime.schedule_conditions import evaluate_conditions
from omnicrawler.runtime.scheduler import ScheduleStore


def test_run_due_runs_in_parallel(tmp_path: Path) -> None:
    config = tmp_path / "project.yaml"
    config.write_text("project: {name: x}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    with ScheduleStore(tmp_path / "sched.sqlite3") as schedules:
        schedules.add("a", config, 60, start_at=100)
        schedules.add("b", config, 60, start_at=100)
        schedules.add("c", config, 60, start_at=100)

        spans: list[tuple[float, float]] = []
        lock = threading.Lock()

        def _executor(_path: Path) -> str:
            start = time.monotonic()
            time.sleep(0.2)  # 长任务：串行时三个区间会首尾相接、互不重叠
            with lock:
                spans.append((start, time.monotonic()))
            return "done"

        started = time.monotonic()
        results = schedules.run_due(_executor, limit=10)
        elapsed = time.monotonic() - started

        assert len(results) == 3
        assert all(item["ok"] for item in results)
        assert len(spans) == 3

        # 判据：存在两个任务的执行区间相交 —— 这才是「并行」的确定性证据。
        # 旧写法是断言 elapsed < 1.0s，但串行 3×0.2s=0.6s 同样能通过，
        # 无法因它声称的原因（未并行）而失败；区间相交与机器速度无关。
        overlapped = any(
            a[0] < b[1] and b[0] < a[1]
            for i, a in enumerate(spans) for b in spans[i + 1:]
        )
        assert overlapped, f"executor 未并发执行（区间无重叠）: {spans}"

        # 宽松上界：**只用于抓整体卡死**，不承担并行判据（那是上面的区间相交）。
        # 按实际工作量派生而不是写死秒数：串行下界是 3×0.2s=0.6s，慢 runner
        # （Windows CI 实测有 6~10 倍倍率）能推到 4s 上下——写死 3.0s 会让这条
        # 「防卡死」断言在只是慢的机器上判红（2026-11-12 windows 腿实测 3.98s，
        # 同时 overlap 判据通过）。30 倍余量仍远小于作业级 timeout，真卡死必响。
        assert elapsed < 0.2 * 3 * 30, f"run_due 耗时异常: {elapsed:.2f}s"



def test_allowed_hours_uses_utc(tmp_path: None = None) -> None:
    from datetime import datetime

    utc_hour = datetime.now(UTC).hour
    allowed, reason = evaluate_conditions({"allowed_hours": [utc_hour]})
    assert allowed is True
    blocked, _reason = evaluate_conditions({"allowed_hours": [(utc_hour + 1) % 24]})
    assert blocked is False

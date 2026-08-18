"""S2.5.26：AutoPilot 双向调整（并发回升 + 磁盘保护生效）。"""

from __future__ import annotations

from omnicrawler.runtime.auto_pilot import AutoPilot


def test_low_load_recovers_concurrency(tmp_path: None = None) -> None:
    pilot = AutoPilot(min_concurrency=1, max_concurrency=8)
    pilot.start(concurrency=4, wait_seconds=1.0)
    # 健康低负载信号（低延迟、低错误率）
    for _ in range(8):
        pilot.record_signals(latency=0.3, error_rate=0.0)
    adjustments = pilot.maybe_adjust()
    assert any(item.parameter == "concurrency" and item.after > item.before for item in adjustments)
    assert pilot._state.current_concurrency > 4


def test_disk_protection_pauses_run(tmp_path: None = None) -> None:
    pilot = AutoPilot(min_concurrency=1, max_concurrency=8)
    pilot.start(concurrency=2, wait_seconds=1.0)
    for _ in range(6):
        pilot.record_signals(latency=0.3, error_rate=0.0, free_disk=100_000)
    adjustments = pilot.maybe_adjust()
    assert any(item.parameter == "run_state" and item.after == "pause" for item in adjustments)
    assert pilot._state.running is False


def test_high_load_still_downsizes(tmp_path: None = None) -> None:
    pilot = AutoPilot(min_concurrency=1, max_concurrency=8)
    pilot.start(concurrency=6, wait_seconds=1.0)
    for _ in range(8):
        pilot.record_signals(latency=3.0, error_rate=0.5, rate_limited=True)
    adjustments = pilot.maybe_adjust()
    assert any(item.parameter == "concurrency" and item.after < item.before for item in adjustments)

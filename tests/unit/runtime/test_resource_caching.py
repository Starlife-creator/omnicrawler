"""S2.5.27：resources rglob 缓存 + AutoPilot/Controller audit 有界。"""

from __future__ import annotations

from pathlib import Path

from omnicrawl.runtime.adaptive_execution import AdaptiveController, RuntimeSignals
from omnicrawl.runtime.auto_pilot import AutoPilot
from omnicrawl.runtime.resources import _SIZE_CACHE, _directory_size


def test_directory_size_cache_avoids_rescan(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    first = _directory_size(tmp_path)
    assert first == 100
    _SIZE_CACHE.pop(tmp_path, None)
    second = _directory_size(tmp_path)
    assert second == 100
    # 修改文件后指纹变化，重新计算
    (tmp_path / "b.txt").write_bytes(b"y" * 50)
    third = _directory_size(tmp_path)
    assert third == 150


def test_controller_audit_is_bounded() -> None:
    controller = AdaptiveController(
        enabled=True, minimum_concurrency=1, maximum_concurrency=8,
    )
    for _ in range(20):
        controller.propose(
            {"concurrency": 4, "wait_seconds": 1.0, "ocr": True},
            RuntimeSignals(
                latency_seconds=0.3, error_rate=0.0, rate_limited=False,
                dom_stability=0.96, text_layer_quality=0.9,
                free_disk_bytes=10_000_000_000,
            ),
        )
    assert len(controller.audit) <= 500
    assert len(controller.audit) == 20 * 2  # 每轮至多 2 条：concurrency + wait_seconds


def test_auto_pilot_audit_is_bounded() -> None:
    pilot = AutoPilot(min_concurrency=1, max_concurrency=8)
    pilot.start(concurrency=2, wait_seconds=1.0)
    for _ in range(30):
        for _ in range(4):
            pilot.record_signals(latency=0.3, error_rate=0.0)
        pilot.maybe_adjust()
    assert len(pilot._state.audit) <= 200

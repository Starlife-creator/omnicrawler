"""S3.2.1 ④：ChangeDetector 基线持久化（无哨兵假哈希）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from omnicrawl.scheduling.change_detector import ChangeDetector, MonitorRule


def _rule(**overrides) -> MonitorRule:
    fields = {
        "url": "https://example.org/page",
        "name": "监控页",
        "selector": "",
        "condition": "changed",
        "check_interval": 0,
    }
    fields.update(overrides)
    return MonitorRule(**fields)


def test_baseline_persists_across_detector_instances(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "monitor"
    first = ChangeDetector(data_dir=data_dir)
    rule = _rule(rule_id="r1")
    first.add_rule(rule)

    async def _content(_self) -> str:
        return "<html>baseline content</html>"

    monkeypatch.setattr(first, "_fetch_content", _content)
    asyncio.run(first.check_all())
    assert rule.last_hash is not None  # 首次建立基线
    assert (data_dir / "baselines.json").is_file()

    # 重建 detector（模拟 GUI 每轮新建）+ 规则对象无 last_hash
    second = ChangeDetector(data_dir=data_dir)
    fresh = _rule(rule_id="r1")  # last_hash=None
    second.add_rule(fresh)
    assert fresh.last_hash == rule.last_hash  # 从磁盘恢复基线

    # 内容未变 → 无变化事件（不再是哨兵误报）
    monkeypatch.setattr(second, "_fetch_content", _content)
    events = asyncio.run(second.check_all())
    assert events == []


def test_content_change_still_detected(tmp_path: Path, monkeypatch) -> None:
    data_dir = tmp_path / "monitor2"
    detector = ChangeDetector(data_dir=data_dir)
    rule = _rule(rule_id="r2")
    detector.add_rule(rule)

    state = {"html": "<html>v1</html>"}

    async def _content(_self) -> str:
        return state["html"]

    monkeypatch.setattr(detector, "_fetch_content", _content)
    assert asyncio.run(detector.check_all()) == []  # 基线
    state["html"] = "<html>v2 changed</html>"
    events = asyncio.run(detector.check_all())
    assert len(events) == 1
    assert events[0].rule_id == "r2"

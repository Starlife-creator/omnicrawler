import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule
from omnicrawler.scheduling.monitor_store import MonitorStore


def test_deleted_baseline_cannot_be_resurrected_by_stale_owner(tmp_path):
    store = MonitorStore(tmp_path)
    with pytest.raises(RuntimeError):
        store.save("r", {"last_content": "new"}, expected={"last_content": "old"})
    assert store.baselines() == {}


def test_conflicting_detector_reloads_authoritative_state_and_recovers(tmp_path):
    first = ChangeDetector(tmp_path, durable_delivery=True)
    first_rule = MonitorRule("https://example.org", rule_id="r", check_interval=0)
    first.add_rule(first_rule)
    first._fetch_content = AsyncMock(return_value="100")
    asyncio.run(first.check_all())
    stale = ChangeDetector(tmp_path, durable_delivery=True)
    stale_rule = MonitorRule("https://example.org", rule_id="r", check_interval=0)
    stale.add_rule(stale_rule)
    first._fetch_content = AsyncMock(return_value="80")
    asyncio.run(first.check_all())
    stale._fetch_content = AsyncMock(return_value="60")
    asyncio.run(stale.check_all())
    assert stale_rule.last_content == "80"
    assert stale._check_results["r"]["status"] == "failed"
    assert stale._check_results["r"]["error_type"] == "BaselineConflictError"
    events = asyncio.run(stale.check_all())
    actual = next(event for event in events if event.current_content == "60")
    assert actual.previous_content == "80"
    assert stale._store.baselines()["r"]["last_content"] == "60"
    assert len(stale._store.observation_report("r")) == 3


def test_legacy_json_baseline_import_preserves_first_comparison(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "baselines.json").write_text(json.dumps({"r": {
        "last_hash": ChangeDetector._compute_hash("100"), "last_content": "100"}}))
    detector = ChangeDetector(tmp_path, durable_delivery=True)
    detector.add_rule(MonitorRule("https://example.org", rule_id="r", check_interval=0))
    detector._fetch_content = AsyncMock(return_value="80")
    event = asyncio.run(detector.check_rule("r"))
    assert event.previous_content == "100"

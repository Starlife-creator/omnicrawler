"""Rule suppression must retain observations and survive detector restart."""
from unittest.mock import AsyncMock

import pytest

from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule


@pytest.mark.asyncio
async def test_consecutive_confirmation_survives_restart(tmp_path):
    detector = ChangeDetector(tmp_path, durable_delivery=True)
    rule = MonitorRule("https://example.org", rule_id="stable", check_interval=0, consecutive_checks=2)
    detector.add_rule(rule)
    detector._fetch_content = AsyncMock(side_effect=["100", "80"])
    assert await detector.check_rule(rule.rule_id) is None
    assert await detector.check_rule(rule.rule_id) is None
    restored = ChangeDetector(tmp_path, durable_delivery=True)
    restored.add_rule(MonitorRule("https://example.org", rule_id="stable", check_interval=0, consecutive_checks=2))
    restored._fetch_content = AsyncMock(return_value="80")
    event = await restored.check_rule("stable")
    assert event.previous_content == "100" and event.current_content == "80"
    assert len(restored._store.observation_report("stable")) == 3


@pytest.mark.asyncio
async def test_cooldown_keeps_fact_and_relative_zero_is_explicit(tmp_path):
    detector = ChangeDetector(tmp_path, durable_delivery=True)
    rule = MonitorRule("https://example.org", rule_id="cool", check_interval=0, cooldown_seconds=3600)
    detector.add_rule(rule)
    detector._fetch_content = AsyncMock(side_effect=["100", "90", "80"])
    await detector.check_rule("cool")
    first = await detector.check_rule("cool")
    second = await detector.check_rule("cool")
    assert first.notification_eligible is True
    assert second.notification_eligible is False
    assert second.notification_reason == "cooldown"
    assert next(r for r in detector.delivery_report() if r["event_id"] == second.event_id)["status"] == "suppressed"

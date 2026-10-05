"""Regression scenarios for retained values and healthy slow-site recovery."""
from omnicrawler.extraction.ai_graph import AIGraphExtractor
from omnicrawler.runtime.live_concurrency import LiveConcurrency


def test_ai_merge_preserves_zero_false_and_detects_their_conflicts():
    result = AIGraphExtractor()._merge_results([
        {"fields": {"price": 0, "available": False}, "confidence": 0.8},
        {"fields": {"price": 12, "available": True}, "confidence": 0.9},
    ], 2)
    assert result["fields"] == {"price": 0, "available": False}
    assert {item["field"] for item in result["conflicts"]} == {"price", "available"}


def test_slow_healthy_site_recovers_after_rate_limit():
    controller = LiveConcurrency(4)
    controller.observe(2.0, rate_limited=True)
    assert controller.current == 3
    for _ in range(32):
        controller.observe(2.0)
    assert controller.current == 4


def test_fast_failures_and_resource_pressure_never_restore_admission():
    controller = LiveConcurrency(4)
    for _ in range(32):
        controller.observe(0.01, failed=True)
    assert controller.current == 1
    for _ in range(32):
        controller.observe(2, resource_pressure=True)
    assert controller.current == 1
    for _ in range(32):
        controller.observe(2)
    assert controller.current == 4

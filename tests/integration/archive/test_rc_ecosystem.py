from __future__ import annotations

from omnicrawler.services.benchmark_corpus import (
    SiteCapsule,
    materialize_capsule,
    minimum_catalog,
    validate_capsule,
)
from omnicrawler.services.benchmarking import BenchmarkResult, compare_benchmark, summarize_benchmarks
from omnicrawler.services.product_metrics import valid_automation
from omnicrawler.services.release_reliability import CanaryObservation, decide_rollout, incident_timeline


def test_minimum_offline_corpus_meets_2_0_scale_and_capsule_contract(tmp_path):
    catalog = minimum_catalog()
    assert len(catalog["web"]) >= 20
    assert len(catalog["interactions"]) >= 10
    assert len(catalog["api"]) >= 10
    assert len(catalog["pdf"]) >= 20
    assert {"zip_bomb", "zip_slip", "csv_formula", "ssrf", "prompt_injection"} <= set(catalog["security"])
    capsule = SiteCapsule(
        "static-article-v1", "web", "<h1>snapshot</h1>", "<h1>dom</h1>", ("style.css",),
        {"log": {"entries": []}}, ({"action": "click", "selector": "a.next"},), ("session",),
        {"records": [{"title": "snapshot"}]}, {"completeness": 1.0}, 10, "",
    )
    path = materialize_capsule(tmp_path / "corpus", capsule)
    assert validate_capsule(path) == ()


def test_benchmark_is_repeatable_and_regressions_are_explicit():
    baseline = BenchmarkResult("standard", 100, 10, 200_000_000, 10_000_000, 0)
    candidate = BenchmarkResult("standard", 100, 12, 220_000_000, 9_000_000, 1)
    summary = summarize_benchmarks([baseline, candidate])
    assert summary["runs"] == 2
    assert summary["peak_memory_bytes"] == 220_000_000
    comparison = compare_benchmark(baseline, candidate)
    assert comparison["regression"] is True
    assert valid_automation(85, 100) == 0.85


def test_canary_holds_promotes_and_rolls_back_with_timeline():
    hold = decide_rollout(CanaryObservation("worker", "1.9", 5, 0, 0, 1))
    promote = decide_rollout(CanaryObservation("worker", "1.9", 100, .01, .001, .9))
    rollback = decide_rollout(CanaryObservation("worker", "1.9", 100, .01, .001, .9, 1))
    assert (hold.action, promote.action, rollback.action) == ("hold", "promote", "rollback")
    events = [{"timestamp": "2026-02-02", "event": "recovered"}, {"timestamp": "2026-01-01", "event": "failed"}]
    assert incident_timeline(events)[0]["event"] == "failed"

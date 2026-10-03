from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.quality.comparison_evidence import ComparisonSample, compare_snapshots
from omnicrawler.quality.llm_candidate_generator import LLMCandidateGenerator
from omnicrawler.quality.shadow_repair import approve_repair, candidate_rule, shadow_config


def _sample(identity: str, html: str, expected: str) -> ComparisonSample:
    response = FetchResult(CrawlRequest("https://example.test/"), "https://example.test/", 200,
                           {"content-type": "text/html; charset=utf-8"}, html.encode(), 0)
    return ComparisonSample(identity, response, ({"title": expected},))


@pytest.fixture
def evidence(tmp_path: Path):
    config = AppConfig(tmp_path / "config.yaml", tmp_path, {
        "extract": {"item_selector": ".card", "fields": {
            "title": {"selector": ".old", "attr": "href"},
        }},
    }, tmp_path)
    candidate = candidate_rule("title", "css", ".old", ".new", ("one",))
    current = _sample("current", '<div class="card"><a class="new" href="/one">noise</a></div>',
                      "https://example.test/one")
    history = _sample("history", '<div class="card"><a class="old new" href="/two">noise</a></div>',
                      "https://example.test/two")
    return config, candidate, current, history


def test_measures_production_attribute_and_item_semantics(evidence):
    config, candidate, current, history = evidence
    comparison = compare_snapshots(config, candidate, [current], [history])
    assert comparison.old_records == 0
    assert comparison.new_records == 1
    assert (comparison.old_quality, comparison.new_quality) == (0, 1)
    assert comparison.improves_safely
    assert comparison.evidence_sha256
    approve_repair(config.raw, shadow_config(config.raw, candidate), candidate, comparison, "reviewer")


def test_current_success_cannot_hide_historical_regression(evidence):
    config, candidate, current, _history = evidence
    history = _sample("history", '<div class="card"><a class="old" href="/two">noise</a></div>',
                      "https://example.test/two")
    comparison = compare_snapshots(config, candidate, [current], [history])
    assert comparison.new_quality == 1
    assert not comparison.historical_compatible
    assert not comparison.improves_safely


def test_extra_records_are_false_matches(evidence):
    config, candidate, current, history = evidence
    current.response.body += b'<div class="card"><a class="new" href="/advert">ad</a></div>'
    comparison = compare_snapshots(config, candidate, [current], [history])
    assert comparison.new_records == 2
    assert comparison.false_matches == 1
    assert not comparison.improves_safely


def test_missing_history_and_duplicate_sample_ids_fail_closed(evidence):
    config, candidate, current, history = evidence
    assert not compare_snapshots(config, candidate, [current], []).improves_safely
    assert not compare_snapshots(config, candidate, [], [history]).improves_safely
    assert not compare_snapshots(config, candidate, [current], [current]).improves_safely


def test_evidence_cannot_be_reused_after_config_changes(evidence):
    config, candidate, current, history = evidence
    comparison = compare_snapshots(config, candidate, [current], [history])
    changed = deepcopy(config.raw)
    changed["extract"]["fields"]["title"]["attr"] = "text"
    with pytest.raises(ValueError, match="重新比较"):
        approve_repair(changed, shadow_config(changed, candidate), candidate, comparison, "reviewer")


def test_generator_uses_measured_evidence_and_ignores_claimed_scores(evidence):
    config, _candidate, current, history = evidence
    generator = LLMCandidateGenerator(llm_generate=lambda _: '{"rule_type":"css","selector":".new"}')
    results = generator.generate_candidates(
        current.response.body.decode(), [ExtractedRecord("https://example.test/", "item", {})],
        config.section("extract")["fields"], active_config=config,
        current_samples=[current], historical_samples=[history], old_quality=0.9, new_quality=0.1,
    )
    assert results
    assert results[0].comparison.new_quality == 1
    assert results[0].comparison.improves_safely

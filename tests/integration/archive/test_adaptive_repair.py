from __future__ import annotations

import copy

import pytest

from omnicrawl.quality.shadow_repair import ShadowComparison, approve_repair, candidate_rule, shadow_config
from omnicrawl.review.review_feedback import FeedbackCorpus, FeedbackSample
from omnicrawl.runtime.adaptive_execution import AdaptiveController, RuntimeSignals, attachment_duplicate


def test_shadow_candidate_never_mutates_active_and_requires_safe_approval():
    active = {"source": {"seeds": ["https://example.com"]}, "extract": {"fields": {"title": {"selector": "h1.old"}}}}
    original = copy.deepcopy(active)
    candidate = candidate_rule("title", "css", "h1.old", "main h1", ("sample-1", "sample-2", "sample-3"))
    shadow = shadow_config(active, candidate)
    assert active == original
    assert shadow["extract"]["fields"]["title"]["selector"] == "main h1"
    unsafe = ShadowComparison(10, 11, 0.7, 0.9, 1, True)
    with pytest.raises(ValueError):
        approve_repair(active, shadow, candidate, unsafe, "reviewer")
    safe = ShadowComparison(10, 10, 0.7, 0.9, 0, True)
    approved = approve_repair(active, shadow, candidate, safe, "reviewer")
    assert approved["_repair"]["rollback_config_sha256"]
    assert approved["_repair"]["status"] == "observing"


def test_adaptive_controller_is_bounded_explainable_reproducible_and_never_deletes():
    controller = AdaptiveController(maximum_concurrency=4, minimum_free_disk=1000)
    current = {"concurrency": 4, "wait_seconds": 2, "ocr": True, "allow_domains": ["example.com"]}
    signals = RuntimeSignals(5, 0.3, True, 0.4, 0.98, 500)
    changes = controller.propose(current, signals)
    mapping = controller.audit_mapping()
    assert {item.parameter for item in changes} == {"concurrency", "wait_seconds", "ocr", "run_state"}
    assert next(item for item in changes if item.parameter == "concurrency").after == 3
    assert all(item["reason"] and "before" in item and "after" in item for item in mapping)
    assert not any(item["parameter"] in {"allow_domains", "seeds", "delete"} for item in mapping)
    assert controller.propose(current, signals) == changes
    disabled = AdaptiveController(enabled=False)
    assert disabled.propose(current, signals) == ()


def test_attachment_dedup_uses_url_metadata_and_hash():
    known = {("https://example.com/a.pdf", '"v1"', "abc")}
    assert attachment_duplicate("https://example.com/a.pdf", {"etag": '"v1"'}, "abc", known)
    assert not attachment_duplicate("https://example.com/a.pdf", {"etag": '"v2"'}, "abc", known)


def test_feedback_becomes_approved_regression_and_long_term_accuracy():
    corpus = FeedbackCorpus()
    corpus.add(FeedbackSample("a", "e1", "wrong", "right", "rule-v1", 0.9, 0.8, True))
    corpus.add(FeedbackSample("b", "e2", "right", "right", "rule-v1", 0.2, 0.1, False))
    assert corpus.review_order()[0].sample_id == "a"
    assert corpus.regression_samples()[0].sample_id == "a"
    assert corpus.accuracy("rule-v1") == {"evaluated": 2, "correct": 1, "accuracy": 0.5}

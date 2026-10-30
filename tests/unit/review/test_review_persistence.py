"""A-P1-3：复核决策 / 反馈语料落 SQLite（复用 record_edits / audit_events）回归测试。"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from omnicrawler.review.review_feedback import FeedbackCorpus, FeedbackSample
from omnicrawler.review.review_persistence import REVIEW_SCHEMA_VERSION, ReviewStore
from omnicrawler.review.review_workbench import ReviewDecision, ReviewQueue
from omnicrawler.state import StateStore

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture()
def store() -> ReviewStore:
    with tempfile.TemporaryDirectory() as temp:
        state = StateStore(Path(temp) / "state.sqlite3")
        try:
            yield ReviewStore(state)
        finally:
            state.close()


def test_review_queue_persists_decision_with_reviewer_hash_version(store: ReviewStore) -> None:
    queue = ReviewQueue(store=store)
    decision = ReviewDecision(
        record_id="rec-1", field="title", old_value="旧标题", new_value="新标题",
        scope="record", reviewer="alice",
    )
    queue.correct(decision)

    assert len(queue.persisted) == 1
    receipt = queue.persisted[0]
    assert receipt["version"] == 1
    assert _HASH_RE.match(receipt["content_hash"])
    assert receipt["created_at"]

    rows = store.decisions("rec-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["reviewer"] == "alice"
    assert row["field"] == "title"
    assert row["scope"] == "record"
    assert row["version"] == 1
    assert row["schema_version"] == REVIEW_SCHEMA_VERSION
    assert _HASH_RE.match(row["content_hash"])
    assert row["created_at"]


def test_decision_version_increments_per_record_id(store: ReviewStore) -> None:
    queue = ReviewQueue(store=store)
    queue.correct(ReviewDecision("rec-9", "price", 1, 2, "record", "bob"))
    queue.correct(ReviewDecision("rec-9", "stock", 0, 3, "similar", "bob"))

    versions = [item["version"] for item in store.decisions("rec-9")]
    assert versions == [1, 2]
    # 不同记录互不干扰版本计数
    queue.correct(ReviewDecision("rec-10", "price", 5, 6, "record", "bob"))
    assert store.decisions("rec-10")[0]["version"] == 1


def test_invalid_scope_rejected_before_persistence(store: ReviewStore) -> None:
    queue = ReviewQueue(store=store)
    with pytest.raises(ValueError):
        queue.correct(ReviewDecision("rec-x", "f", None, None, "bogus", "carol"))
    assert queue.persisted == []
    assert store.decisions("rec-x") == []


def test_feedback_corpus_persists_sample_with_hash_version(store: ReviewStore) -> None:
    corpus = FeedbackCorpus(store=store)
    sample = FeedbackSample(
        sample_id="fb-1", input_evidence="标题字段", predicted="A", corrected="B",
        rule_or_model="rule-v1", risk=0.8, information_gain=0.4, approved_for_regression=True,
    )
    corpus.add(sample)

    assert corpus.persisted[0]["version"] == 1
    rows = store.feedback("rule-v1")
    assert len(rows) == 1
    assert rows[0]["sample_id"] == "fb-1"
    assert rows[0]["corrected"] == "B"
    assert rows[0]["approved_for_regression"] is True
    assert rows[0]["version"] == 1
    assert _HASH_RE.match(rows[0]["content_hash"])
    assert len(store.feedback()) == 1


def test_queue_without_store_stays_in_memory() -> None:
    queue = ReviewQueue()
    queue.correct(ReviewDecision("rec-mem", "f", None, None, "record", "dave"))
    assert queue.persisted == []
    assert len(queue.decisions) == 1

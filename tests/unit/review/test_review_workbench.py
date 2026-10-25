"""C-#16：复核工作台排序 / 决策口径 / 反馈语料准确率与排序直接单测（纯内存）。"""
from __future__ import annotations

import pytest

from omnicrawler.review.review_feedback import FeedbackCorpus, FeedbackSample
from omnicrawler.review.review_workbench import ReviewDecision, ReviewItem, ReviewQueue


def _item(record_id: str, *, missing_required: tuple[str, ...] = (), rule_conflicts: int = 0) -> ReviewItem:
    return ReviewItem(
        record_id=record_id, source_url="https://example.com", fields=[],
        missing_required=missing_required, rule_conflicts=rule_conflicts,
    )


def test_ranked_orders_by_risk_score_desc_then_record_id() -> None:
    low = _item("r-low")
    high = _item("r-high", missing_required=("title",))
    queue = ReviewQueue([low, high])
    ranked = queue.ranked()
    assert ranked[0].record_id == "r-high"
    assert ranked[1].record_id == "r-low"


def test_correct_rejects_unsupported_scope_and_filters_regression() -> None:
    queue = ReviewQueue([_item("r1")])
    decision = ReviewDecision(
        record_id="r1", field="title", old_value="a", new_value="b",
        scope="record", reviewer="alice",
    )
    queue.correct(decision)
    assert queue.decisions == [decision]

    regression = ReviewDecision(
        record_id="r1", field="price", old_value="1", new_value="2",
        scope="regression", reviewer="bob",
    )
    queue.correct(regression)
    assert queue.regression_samples() == (regression,)

    with pytest.raises(ValueError, match="不支持的复核影响范围"):
        ReviewQueue([_item("r1")]).correct(
            ReviewDecision(
                record_id="r1", field="x", old_value=None, new_value=None,
                scope="unknown", reviewer="c",
            )
        )


def test_feedback_accuracy_per_model_and_empty_returns_none() -> None:
    corpus = FeedbackCorpus()
    corpus.add(FeedbackSample("s1", "e", "A", "A", "rule", 0.2, 0.1, True))
    corpus.add(FeedbackSample("s2", "e", "B", "C", "rule", 0.5, 0.2, False))
    corpus.add(FeedbackSample("s3", "e", "D", "D", "model", 0.9, 0.3, True))

    rule = corpus.accuracy("rule")
    assert rule["evaluated"] == 2 and rule["correct"] == 1 and rule["accuracy"] == 0.5
    model = corpus.accuracy("model")
    assert model["accuracy"] == 1.0
    assert corpus.accuracy("missing")["accuracy"] is None


def test_feedback_review_order_weights_risk_and_information_gain() -> None:
    corpus = FeedbackCorpus()
    corpus.add(FeedbackSample("low", "e", "a", "b", "rule", risk=0.1, information_gain=0.0, approved_for_regression=False))
    corpus.add(FeedbackSample("high", "e", "c", "d", "rule", risk=0.9, information_gain=0.5, approved_for_regression=True))
    ordered = corpus.review_order()
    assert ordered[0].sample_id == "high"
    assert corpus.regression_samples() == (ordered[0],)


def test_risk_score_aggregates_missing_required_and_conflicts() -> None:
    item = _item("r1", missing_required=("title", "price"), rule_conflicts=1)
    # 默认权重：missing_required=30、rule_conflict=20
    assert item.risk_score == 30 * 2 + 20

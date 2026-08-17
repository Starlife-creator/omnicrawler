"""Human-review feedback corpus and longitudinal accuracy metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FeedbackSample:
    sample_id: str
    input_evidence: str
    predicted: Any
    corrected: Any
    rule_or_model: str
    risk: float
    information_gain: float
    approved_for_regression: bool


class FeedbackCorpus:
    def __init__(self) -> None:
        self.samples: list[FeedbackSample] = []

    def add(self, sample: FeedbackSample) -> None:
        self.samples.append(sample)

    def review_order(self) -> tuple[FeedbackSample, ...]:
        return tuple(sorted(self.samples, key=lambda item: (-(item.risk * 0.7 + item.information_gain * 0.3), item.sample_id)))

    def regression_samples(self) -> tuple[FeedbackSample, ...]:
        return tuple(item for item in self.samples if item.approved_for_regression)

    def accuracy(self, rule_or_model: str) -> dict[str, Any]:
        selected = [item for item in self.samples if item.rule_or_model == rule_or_model]
        correct = sum(item.predicted == item.corrected for item in selected)
        return {"evaluated": len(selected), "correct": correct, "accuracy": correct / len(selected) if selected else None}


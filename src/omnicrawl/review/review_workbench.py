"""Risk-ranked professional review model with explicit fact/provenance layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Origin = Literal["raw", "rule", "ai", "human"]


@dataclass(frozen=True, slots=True)
class ReviewField:
    name: str
    value: Any
    origin: Origin
    evidence: str
    confidence: float = 1.0
    page: int | None = None
    historical_value: Any = None


@dataclass(slots=True)
class ReviewItem:
    record_id: str
    source_url: str
    fields: list[ReviewField]
    missing_required: tuple[str, ...] = ()
    rule_conflicts: int = 0
    ai_conflicts: int = 0
    structure_drift: float = 0.0
    ocr_quality: float = 1.0
    duplicate: bool = False
    pending_deletion: bool = False

    @property
    def risk_score(self) -> float:
        return round(
            len(self.missing_required) * 30 + self.rule_conflicts * 20 + self.ai_conflicts * 15
            + self.structure_drift * 20 + (1 - self.ocr_quality) * 20
            + int(self.duplicate) * 10 + int(self.pending_deletion) * 25,
            3,
        )


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    record_id: str
    field: str
    old_value: Any
    new_value: Any
    scope: Literal["record", "similar", "rule_suggestion", "regression", "reprocess"]
    reviewer: str


class ReviewQueue:
    def __init__(self, items: list[ReviewItem] | None = None) -> None:
        self.items = items or []
        self.decisions: list[ReviewDecision] = []

    def ranked(self) -> list[ReviewItem]:
        return sorted(self.items, key=lambda item: (-item.risk_score, item.record_id))

    def correct(self, decision: ReviewDecision) -> None:
        if decision.scope not in {"record", "similar", "rule_suggestion", "regression", "reprocess"}:
            raise ValueError("不支持的复核影响范围")
        self.decisions.append(decision)

    def regression_samples(self) -> tuple[ReviewDecision, ...]:
        return tuple(item for item in self.decisions if item.scope in {"regression", "rule_suggestion"})


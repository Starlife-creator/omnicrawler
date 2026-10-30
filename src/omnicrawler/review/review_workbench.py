"""Risk-ranked professional review model with explicit fact/provenance layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..core.config import DEFAULTS
from .review_models import ReviewDecision
from .review_persistence import ReviewStore

Origin = Literal["raw", "rule", "ai", "human"]

# A-P3-5：复核风险评分权重集中到 core/config.py DEFAULTS["tuning"]["review_weights"]。
_REVIEW_WEIGHTS: dict[str, float] = dict(DEFAULTS["tuning"]["review_weights"])


def configure_review_weights(overrides: dict[str, float] | None = None) -> None:
    """按配置覆盖复核风险权重（仅允许既有键；数值转 float，非法键忽略）。"""
    if not overrides:
        return
    for key, value in overrides.items():
        if key in _REVIEW_WEIGHTS:
            _REVIEW_WEIGHTS[key] = float(value)


@dataclass(frozen=True, slots=True)
class ReviewField:
    name: str
    value: Any
    origin: Origin
    evidence: str
    confidence: float | None = 1.0
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
        w = _REVIEW_WEIGHTS
        return round(
            len(self.missing_required) * w["missing_required"]
            + self.rule_conflicts * w["rule_conflict"]
            + self.ai_conflicts * w["ai_conflict"]
            + self.structure_drift * w["structure_drift"]
            + (1 - self.ocr_quality) * w["ocr_quality"]
            + int(self.duplicate) * w["duplicate"]
            + int(self.pending_deletion) * w["pending_deletion"],
            3,
        )


class ReviewQueue:
    def __init__(
        self,
        items: list[ReviewItem] | None = None,
        *,
        store: ReviewStore | None = None,
    ) -> None:
        self.items = items or []
        self.decisions: list[ReviewDecision] = []
        self.store = store
        #: A-P1-3：每条决策落库后的版本号 / 内容哈希 / 时间戳回执。
        self.persisted: list[dict[str, Any]] = []

    def ranked(self) -> list[ReviewItem]:
        return sorted(self.items, key=lambda item: (-item.risk_score, item.record_id))

    def correct(self, decision: ReviewDecision) -> None:
        if decision.scope not in {"record", "similar", "rule_suggestion", "regression", "reprocess"}:
            raise ValueError("不支持的复核影响范围")
        self.decisions.append(decision)
        if self.store is not None:
            self.persisted.append(self.store.save_decision(decision))

    def regression_samples(self) -> tuple[ReviewDecision, ...]:
        return tuple(item for item in self.decisions if item.scope in {"regression", "rule_suggestion"})


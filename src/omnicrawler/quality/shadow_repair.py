"""Evidence-gated shadow repair; candidates never mutate the active task."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

RuleType = Literal["css", "xpath", "jsonpath", "action"]


@dataclass(frozen=True, slots=True)
class RepairCandidate:
    candidate_id: str
    field: str
    rule_type: RuleType
    old_rule: str
    new_rule: str
    confidence: float
    supporting_samples: tuple[str, ...]
    counterexamples: tuple[str, ...]
    expected_recovery: float
    false_positive_risk: float
    observation_rounds: int = 0

    @property
    def stable(self) -> bool:
        return self.observation_rounds >= 3 and self.false_positive_risk <= 0.1


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    old_records: int
    new_records: int
    old_quality: float
    new_quality: float
    false_matches: int
    historical_compatible: bool

    @property
    def improves_safely(self) -> bool:
        return self.new_quality > self.old_quality and self.false_matches == 0 and self.historical_compatible


def candidate_rule(field: str, rule_type: str, old_rule: str, new_rule: str, supporting: tuple[str, ...], counterexamples: tuple[str, ...] = ()) -> RepairCandidate:
    if rule_type not in {"css", "xpath", "jsonpath", "action"}:
        raise ValueError("不支持的修复规则类型")
    digest = hashlib.sha256(f"{field}:{rule_type}:{old_rule}:{new_rule}".encode()).hexdigest()[:20]
    support = len(supporting)
    risk = len(counterexamples) / max(1, support + len(counterexamples))
    confidence = min(0.99, support / max(1, support + 2)) * (1 - risk)
    return RepairCandidate(digest, field, cast(RuleType, rule_type), old_rule, new_rule, round(confidence, 3), supporting, counterexamples, round(confidence, 3), round(risk, 3))


def shadow_config(active: dict[str, Any], candidate: RepairCandidate) -> dict[str, Any]:
    """Return an isolated candidate config; active remains byte-for-byte equivalent."""
    result = copy.deepcopy(active)
    extract = result.setdefault("extract", {})
    fields = extract.setdefault("fields", {})
    spec = fields.setdefault(candidate.field, {})
    key = "selector" if candidate.rule_type == "css" else candidate.rule_type
    spec[key] = candidate.new_rule
    result.setdefault("_shadow", {})["candidate_id"] = candidate.candidate_id
    return result


def approve_repair(active: dict[str, Any], shadow: dict[str, Any], candidate: RepairCandidate, comparison: ShadowComparison, approved_by: str) -> dict[str, Any]:
    if not approved_by or not comparison.improves_safely:
        raise ValueError("修复必须经人工批准且影子比较安全改善")
    approved = copy.deepcopy(shadow)
    approved.pop("_shadow", None)
    snapshot = hashlib.sha256(json.dumps(active, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    approved["_repair"] = {
        "candidate_id": candidate.candidate_id, "approved_by": approved_by,
        "rollback_config_sha256": snapshot, "status": "observing" if not candidate.stable else "stable",
    }
    return approved

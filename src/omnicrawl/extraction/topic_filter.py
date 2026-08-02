"""Deterministic topic matching shared by simple and advanced configurations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TopicDecision:
    matched: bool
    uncertain: bool
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    reason: str


def evaluate_topic(value: Any, config: dict[str, Any]) -> TopicDecision:
    include_any = _terms(config.get("include_any", []))
    include_all = _terms(config.get("include_all", []))
    exclude = _terms(config.get("exclude", []))
    text = _text(value, config.get("match_on", []))
    excluded = tuple(term for term in exclude if term in text)
    any_hits = tuple(term for term in include_any if term in text)
    all_hits = tuple(term for term in include_all if term in text)
    if excluded:
        return TopicDecision(False, False, any_hits + all_hits, excluded, "命中排除词")
    if include_all and len(all_hits) != len(include_all):
        return TopicDecision(False, not bool(text), any_hits + all_hits, (), "未命中全部必含词")
    if include_any and not any_hits:
        return TopicDecision(False, not bool(text), (), (), "未命中任一主题词")
    if not include_any and not include_all:
        return TopicDecision(True, False, (), (), "未设置主题限制")
    return TopicDecision(True, False, any_hits + all_hits, (), "主题匹配")


def filter_records(records: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    if not config.get("enabled", False):
        return records
    keep_uncertain = bool(config.get("keep_uncertain", True))
    filtered: list[dict[str, Any]] = []
    for record in records:
        decision = evaluate_topic(record, config)
        record["_topic_match"] = {
            "matched": decision.matched,
            "uncertain": decision.uncertain,
            "included": list(decision.included),
            "excluded": list(decision.excluded),
            "reason": decision.reason,
        }
        if decision.matched or (decision.uncertain and keep_uncertain):
            filtered.append(record)
    return filtered


def _terms(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item).strip().casefold() for item in value if str(item).strip()))


def _text(value: Any, match_on: Any) -> str:
    fields = {str(item) for item in match_on} if isinstance(match_on, list) else set()
    parts: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            if fields and str(key) not in fields:
                continue
            if isinstance(item, (str, int, float, bool)):
                parts.append(str(item))
    elif value is not None:
        parts.append(str(value))
    return " ".join(parts).casefold()

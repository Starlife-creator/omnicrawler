"""Stable entities, bitemporal facts and candidate business events."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

EventType = Literal["created", "unchanged", "withdrawn", "reappeared", "advanced", "postponed", "amount_changed", "status_changed", "field_changed", "source_conflict"]


@dataclass(frozen=True, slots=True)
class TemporalFact:
    entity_id: str
    field: str
    value: Any
    valid_from: str
    observed_at: str
    source_url: str
    evidence_id: str
    unit: str | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class BusinessEvent:
    entity_id: str
    event_type: EventType
    field: str
    before: Any
    after: Any
    observed_at: str
    confidence: float | None
    inference_source: str = "deterministic_rule_unvalidated"


def stable_entity_id(namespace: str, source_key: str) -> str:
    return f"{namespace}:" + hashlib.sha256(source_key.strip().casefold().encode()).hexdigest()[:24]


def infer_business_event(before: TemporalFact | None, after: TemporalFact) -> BusinessEvent:
    if before is not None and (before.entity_id != after.entity_id or before.field != after.field):
        raise ValueError("只能比较相同实体的相同字段")
    from .semantic_changes import values_equal

    equal = before is not None and values_equal(before.value, after.value) and before.unit == after.unit and before.currency == after.currency
    if before is not None and after.field in {"amount", "price", "budget"}:
        equal = _amount_equal(before, after)
    if before is None:
        event_type: EventType = "created"
    elif equal:
        event_type = "unchanged"
    elif before.source_url != after.source_url:
        event_type = "source_conflict"
    elif after.field in {"amount", "price", "budget"}:
        event_type = "amount_changed"
    elif after.field in {"status", "state"}:
        value = str(after.value).casefold()
        withdrawn = any(word in value for word in ("withdraw", "撤回", "取消"))
        was_withdrawn = any(word in str(before.value).casefold() for word in ("withdraw", "撤回", "取消"))
        event_type = "withdrawn" if withdrawn else "reappeared" if was_withdrawn else "status_changed"
    elif after.field in {"deadline", "date"}:
        event_type = _date_event(before.value, after.value)
    else:
        event_type = "field_changed"
    return BusinessEvent(after.entity_id, event_type, after.field, before.value if before else None, after.value, after.observed_at, None)


def _amount_equal(before: TemporalFact, after: TemporalFact) -> bool:
    units = {"元": Decimal(1), "万元": Decimal(10000), "亿元": Decimal(100000000)}
    if before.unit is None and after.unit is None:
        from .semantic_changes import values_equal

        return before.currency == after.currency and values_equal(before.value, after.value)
    if before.currency != after.currency or before.unit not in units or after.unit not in units:
        return False
    if isinstance(before.value, bool) or isinstance(after.value, bool):
        return type(before.value) is type(after.value) and before.value == after.value and before.unit == after.unit
    try:
        left, right = Decimal(str(before.value)), Decimal(str(after.value))
        return left.is_finite() and right.is_finite() and left * units[before.unit] == right * units[after.unit]
    except InvalidOperation:
        return False


def _date_event(before: Any, after: Any) -> EventType:
    try:
        left = datetime.fromisoformat(str(before).replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(after).replace("Z", "+00:00"))
        return "unchanged" if left == right else "postponed" if right > left else "advanced"
    except (ValueError, TypeError):
        return "field_changed"


class EntityRegistry:
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}

    def resolve(self, entity_id: str) -> str:
        seen = set()
        while entity_id in self.aliases:
            if entity_id in seen:
                raise ValueError("实体别名存在循环")
            seen.add(entity_id)
            entity_id = self.aliases[entity_id]
        return entity_id

    def merge(self, alias: str, canonical: str) -> None:
        if alias == canonical:
            return
        resolved = self.resolve(canonical)
        if resolved == alias:
            raise ValueError("实体别名存在循环")
        self.aliases[alias] = resolved

    def split(self, alias: str) -> None:
        self.aliases.pop(alias, None)

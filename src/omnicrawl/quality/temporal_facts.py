"""Stable entities, bitemporal facts and candidate business events."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

EventType = Literal["created", "withdrawn", "postponed", "amount_changed", "status_changed", "field_changed", "source_conflict"]


@dataclass(frozen=True, slots=True)
class TemporalFact:
    entity_id: str
    field: str
    value: Any
    valid_from: str
    observed_at: str
    source_url: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class BusinessEvent:
    entity_id: str
    event_type: EventType
    field: str
    before: Any
    after: Any
    observed_at: str
    confidence: float


def stable_entity_id(namespace: str, source_key: str) -> str:
    return f"{namespace}:" + hashlib.sha256(source_key.strip().casefold().encode()).hexdigest()[:24]


def infer_business_event(before: TemporalFact | None, after: TemporalFact) -> BusinessEvent:
    if before is None:
        event_type: EventType = "created"
    elif before.source_url != after.source_url and before.value != after.value:
        event_type = "source_conflict"
    elif after.field in {"amount", "price", "budget"}:
        event_type = "amount_changed"
    elif after.field in {"status", "state"}:
        value = str(after.value).casefold()
        event_type = "withdrawn" if any(word in value for word in ("withdraw", "撤回", "取消")) else "status_changed"
    elif after.field in {"deadline", "date"}:
        event_type = "postponed"
    else:
        event_type = "field_changed"
    return BusinessEvent(after.entity_id, event_type, after.field, before.value if before else None, after.value, after.observed_at, 0.9)


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
        self.aliases[alias] = self.resolve(canonical)
        self.resolve(alias)

    def split(self, alias: str) -> None:
        self.aliases.pop(alias, None)

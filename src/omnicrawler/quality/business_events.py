"""Explicit task field mappings for deterministic business event annotations."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .temporal_facts import TemporalFact, infer_business_event

EVENT_TYPES = {"created", "unchanged", "withdrawn", "reappeared", "advanced", "postponed", "amount_changed",
               "status_changed", "field_changed", "field_removed"}


def mapped_events(before: dict[str, Any] | None, after: dict[str, Any], mappings: dict[str, Any], *,
                  identity: str, source_url: str, observed_at: str) -> list[dict[str, Any]]:
    result = []
    for name, rule in mappings.items():
        if name not in after and (before is None or name not in before):
            continue
        def fact(data: dict[str, Any], rule: dict[str, Any] = rule, name: str = name) -> TemporalFact:
            unit = data.get(rule["unit_field"]) if rule.get("unit_field") else None
            currency = data.get(rule["currency_field"]) if rule.get("currency_field") else None
            return TemporalFact(identity, rule["kind"], data.get(name), "", observed_at, source_url, "",
                                unit=unit if isinstance(unit, str) else None,
                                currency=currency if isinstance(currency, str) else None)
        prior = fact(before) if before is not None and name in before else None
        event = asdict(infer_business_event(prior, fact(after)))
        event["field"] = name
        event["before_present"] = before is not None and name in before
        event["after_present"] = name in after
        if name not in after:
            event["event_type"] = "field_removed"
        elif after[name] is None and before is not None and prior is not None:
            event["event_type"] = "unchanged" if before[name] is None else "field_changed"
        elif any(key in rule and (before is not None and not isinstance(before.get(rule[key]), str) or not isinstance(after.get(rule[key]), str))
                 for key in ("unit_field", "currency_field")):
            event["event_type"] = "field_changed"
        event["valid_time"] = "unknown"
        event["source_url"] = source_url
        result.append(event)
    return result

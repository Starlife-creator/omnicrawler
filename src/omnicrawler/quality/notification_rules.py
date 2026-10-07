"""Explicit record notification policy validation and numeric thresholds."""
from __future__ import annotations

import math
from typing import Any

from .business_events import EVENT_TYPES
from .semantic_changes import values_equal


def validate_policy(policy: Any) -> list[str]:
    if not isinstance(policy, dict):
        return ["通知policy必须是对象"]
    errors = []
    if set(policy) - {"fields", "confirmations", "cooldown_seconds", "semantic_fields", "event_types"}:
        errors.append("通知policy包含未知规则")
    semantics = policy.get("semantic_fields", {})
    if not isinstance(semantics, dict) or len(semantics) > 1000:
        errors.append("通知semantic_fields必须是最多1000个字段的对象")
    else:
        for name, rule in semantics.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(rule, dict):
                errors.append("通知语义字段须使用非空字段名和对象")
                continue
            if set(rule) - {"kind", "unit_field", "currency_field"} or rule.get("kind") not in ("deadline", "date", "status", "amount", "price", "budget"):
                errors.append(f"通知语义字段{name}的kind或规则无效")
            for key in ("unit_field", "currency_field"):
                if key in rule and (not isinstance(rule[key], str) or not rule[key].strip() or rule.get("kind") not in ("amount", "price", "budget")):
                    errors.append(f"通知语义字段{name}的{key}无效")
    if "event_types" in policy:
        types = policy["event_types"]
        if not semantics or not isinstance(types, list) or not types or any(not isinstance(item, str) or item not in EVENT_TYPES - {"unchanged"} for item in types):
            errors.append("通知event_types须为已声明语义字段的非空事件类型列表，不含unchanged")
    for name, default, lower, upper in (("confirmations", 1, 1, 100), ("cooldown_seconds", 0, 0, 604800)):
        value = policy.get(name, default)
        if type(value) is not int or not lower <= value <= upper:
            errors.append(f"通知{name}必须是{lower}至{upper}的整数")
    fields = policy.get("fields", {})
    if not isinstance(fields, dict) or len(fields) > 1000:
        return [*errors, "通知fields必须是最多1000个字段的对象"]
    for name, rule in fields.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(rule, dict):
            errors.append("通知字段规则须使用非空字段名和对象")
            continue
        if set(rule) - {"minimum_absolute_change", "minimum_relative_change", "direction"}:
            errors.append(f"通知字段{name}包含未知规则")
        if rule.get("direction", "any") not in ("any", "increase", "decrease"):
            errors.append(f"通知字段{name}的direction无效")
        for key in ("minimum_absolute_change", "minimum_relative_change"):
            if key not in rule:
                continue
            value = rule[key]
            try:
                valid = type(value) in {int, float} and math.isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                errors.append(f"通知字段{name}的{key}必须是非负有限数字")
    return errors


def threshold_reason(before: dict[str, Any], after: dict[str, Any], fields: dict[str, Any]) -> str:
    """Any watched field can trigger; thresholds within one field all apply."""
    reasons = []
    for name, rule in fields.items():
        if (name in before) == (name in after) and values_equal(before.get(name), after.get(name)):
            continue
        numeric = any(key in rule for key in ("minimum_absolute_change", "minimum_relative_change"))
        numeric = numeric or rule.get("direction", "any") != "any"
        if not numeric:
            return ""
        try:
            if type(before.get(name)) is bool or type(after.get(name)) is bool:
                raise ValueError("bool is not numeric")
            old, new = float(before[name]), float(after[name])
            if not math.isfinite(old) or not math.isfinite(new):
                raise ValueError("non-finite value")
        except (KeyError, ValueError, TypeError, OverflowError):
            reasons.append("numeric_value_unavailable")
            continue
        delta = new - old
        direction = rule.get("direction", "any")
        if direction == "increase" and delta <= 0 or direction == "decrease" and delta >= 0:
            reasons.append("direction_not_matched")
        elif abs(delta) < rule.get("minimum_absolute_change", 0):
            reasons.append("below_absolute_threshold")
        elif "minimum_relative_change" in rule and old == 0:
            reasons.append("relative_baseline_zero")
        elif "minimum_relative_change" in rule and abs(delta) / abs(old) < rule["minimum_relative_change"]:
            reasons.append("below_relative_threshold")
        else:
            return ""
    return reasons[0] if reasons else "watched_fields_unchanged"

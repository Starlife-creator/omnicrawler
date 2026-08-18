from __future__ import annotations

import re
import statistics
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..core.models import ExtractedRecord
from ..core.safe_data import safe_regex_search


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _required_fields(record: ExtractedRecord, fields: dict[str, Any]) -> list[str]:
    required: list[str] = []
    for name, rule in fields.items():
        if not isinstance(rule, dict):
            continue
        condition = rule.get("required_if")
        condition_met = False
        if isinstance(condition, dict) and condition.get("field"):
            other = record.data.get(str(condition["field"]))
            if "equals" in condition:
                condition_met = other == condition["equals"]
            elif "in" in condition and isinstance(condition["in"], list):
                condition_met = other in condition["in"]
            else:
                condition_met = not _missing(other)
        if rule.get("required") or condition_met:
            required.append(str(name))
    return required


def _compare_fields(
    record: ExtractedRecord,
    name: str,
    value: Any,
    rule: dict[str, Any],
    errors: list[str],
) -> None:
    equals = rule.get("equals_field")
    if equals and value != record.data.get(str(equals)):
        errors.append(f"{name}: must equal field {equals}")
    differs = rule.get("not_equals_field")
    if differs and value == record.data.get(str(differs)):
        errors.append(f"{name}: must differ from field {differs}")
    comparisons: tuple[tuple[str, Callable[[float, float], bool]], ...] = (
        ("gt_field", lambda left, right: left > right),
        ("gte_field", lambda left, right: left >= right),
        ("lt_field", lambda left, right: left < right),
        ("lte_field", lambda left, right: left <= right),
    )
    for operator, predicate in comparisons:
        other_name = rule.get(operator)
        if not other_name or _missing(record.data.get(str(other_name))):
            continue
        try:
            left = float(str(value).replace(",", ""))
            right = float(str(record.data[str(other_name)]).replace(",", ""))
        except (TypeError, ValueError):
            errors.append(f"{name}: cannot compare numerically with field {other_name}")
        else:
            if not predicate(left, right):
                errors.append(f"{name}: violates {operator}={other_name}")


def assess_record(
    record: ExtractedRecord,
    fields: dict[str, Any],
    threshold: float = 0.8,
) -> dict[str, Any]:
    required = _required_fields(record, fields)
    missing = [name for name in required if _missing(record.data.get(name))]
    errors: list[str] = []
    present = 0
    for name, raw_rule in fields.items():
        field_name = str(name)
        value = record.data.get(field_name)
        if not _missing(value):
            present += 1
        if not isinstance(raw_rule, dict) or _missing(value):
            continue
        rule = raw_rule
        # B06-002：pattern 匹配统一走 safe_regex_search（与 normalizers 对齐），防病态正则自 DOS。
        if rule.get("pattern") and not safe_regex_search(str(rule["pattern"]), str(value)):
            errors.append(f"{field_name}: does not match pattern")
        expected = str(rule.get("type", "string")).casefold()
        if expected in {"int", "integer"}:
            try:
                int(str(value).replace(",", ""))
            except ValueError:
                errors.append(f"{field_name}: is not an integer")
        elif expected in {"float", "number", "money"}:
            try:
                numeric_text = re.sub(r"[^0-9.+-]", "", str(value).replace(",", ""))
                numeric = float(numeric_text)
                if rule.get("min") is not None and numeric < float(rule["min"]):
                    errors.append(f"{field_name}: below minimum")
                if rule.get("max") is not None and numeric > float(rule["max"]):
                    errors.append(f"{field_name}: above maximum")
            except ValueError:
                errors.append(f"{field_name}: is not numeric")
        elif expected in {"date", "datetime"}:
            try:
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{field_name}: is not an ISO date or datetime")
        elif expected == "enum":
            choices = {str(item) for item in rule.get("values", [])}
            if choices and str(value) not in choices:
                errors.append(f"{field_name}: is outside the enum")
        if rule.get("min_length") is not None and len(str(value)) < int(rule["min_length"]):
            errors.append(f"{field_name}: shorter than minimum length")
        if rule.get("max_length") is not None and len(str(value)) > int(rule["max_length"]):
            errors.append(f"{field_name}: longer than maximum length")
        _compare_fields(record, field_name, value, rule, errors)

    total = max(1, len(fields))
    completeness = present / total
    required_score = 1.0 if not required else (len(required) - len(missing)) / len(required)
    score = max(
        0.0,
        min(1.0, completeness * 0.4 + required_score * 0.6 - min(0.5, len(errors) * 0.1)),
    )
    return {
        "score": round(score, 4),
        "completeness": round(completeness, 4),
        "missing_required": missing,
        "validation_errors": errors,
        "review_required": bool(missing or errors or score < threshold),
    }


def _annotate_anomalies(records: list[ExtractedRecord], fields: dict[str, Any]) -> int:
    anomalies = 0
    for name, rule in fields.items():
        if not isinstance(rule, dict) or not rule.get("anomaly", False):
            continue
        numeric: list[tuple[ExtractedRecord, float]] = []
        for record in records:
            try:
                value = float(str(record.data.get(str(name), "")).replace(",", ""))
            except (TypeError, ValueError):
                continue
            numeric.append((record, value))
        minimum = max(3, int(rule.get("anomaly_min_samples", 5)))
        if len(numeric) < minimum:
            continue
        values = [value for _record, value in numeric]
        deviation = statistics.pstdev(values)
        if deviation == 0:
            continue
        center = statistics.fmean(values)
        threshold_z = max(0.1, float(rule.get("anomaly_zscore", 3.0)))
        for record, value in numeric:
            zscore = abs(value - center) / deviation
            if zscore <= threshold_z:
                continue
            quality = record.evidence["_quality"]
            quality.setdefault("anomalies", []).append(
                {"field": str(name), "value": value, "zscore": round(zscore, 4)}
            )
            quality["review_required"] = True
            anomalies += 1
    return anomalies


def assess_records(
    records: list[ExtractedRecord],
    fields: dict[str, Any],
    threshold: float = 0.8,
    unique_by: list[str] | None = None,
) -> dict[str, Any]:
    duplicates = 0
    seen: set[tuple[str, ...]] = set()
    for record in records:
        quality = assess_record(record, fields, threshold)
        prior_quality = record.evidence.get("_quality", {})
        if isinstance(prior_quality, dict):
            for key, value in prior_quality.items():
                if key == "review_required":
                    quality[key] = bool(quality[key] or value)
                elif key not in quality:
                    quality[key] = value
        if unique_by:
            key = tuple(str(record.data.get(name, "")) for name in unique_by)
            if key in seen:
                quality["duplicate"] = True
                quality["review_required"] = True
                duplicates += 1
            else:
                seen.add(key)
        record.evidence["_quality"] = quality

    anomalies = _annotate_anomalies(records, fields)
    field_stats: dict[str, dict[str, int]] = {}
    for name in fields:
        field_name = str(name)
        present = sum(not _missing(record.data.get(field_name)) for record in records)
        invalid = sum(
            any(
                str(error).startswith(f"{field_name}:")
                for error in record.evidence["_quality"]["validation_errors"]
            )
            for record in records
        )
        field_anomalies = sum(
            any(
                item.get("field") == field_name
                for item in record.evidence["_quality"].get("anomalies", [])
            )
            for record in records
        )
        field_stats[field_name] = {
            "total": len(records),
            "present": present,
            "valid": max(0, present - invalid),
            "anomalies": field_anomalies,
        }
    review = sum(int(record.evidence["_quality"]["review_required"]) for record in records)
    return {
        "records": len(records),
        "review_required": review,
        "duplicates": duplicates,
        "anomalies": anomalies,
        "field_stats": field_stats,
    }

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..core.models import ExtractedRecord
from ..core.safe_data import safe_regex_search
from .schema_registry import validate_target_fields


def _numeric_value(value: Any, *, money: bool = False) -> Decimal:
    """Read a finite decimal without deleting units or arbitrary characters."""
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    text = str(value).strip()
    if len(text) > 4096:
        raise ValueError("numeric value is too long")
    pattern = r"[+-]?(?:(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
    if not re.fullmatch(pattern, text):
        if not money:
            raise ValueError("not a decimal literal")
        from .normalizers import parse_money

        text = str(parse_money(text))
        if not re.fullmatch(pattern, text):
            raise ValueError("unrecognized money value")
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if not number.is_finite():
        raise ValueError("non-finite number")
    return number


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _field_missing(data: dict[str, Any], name: str, rule: Any) -> bool:
    if name not in data:
        return True
    value = data[name]
    if isinstance(rule, dict) and rule.get("strict_json"):
        if value is None and rule.get("nullable") is True:
            return False
        if (value == "" or value == []) and rule.get("allow_empty"):
            return False
    return _missing(value)


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
    comparisons: tuple[tuple[str, Callable[[Decimal, Decimal], bool]], ...] = (
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
            left = _numeric_value(value, money=rule.get("type") == "money")
            right = _numeric_value(record.data[str(other_name)], money=rule.get("type") == "money")
        except (TypeError, ValueError):
            errors.append(f"{name}: cannot compare numerically with field {other_name}")
        else:
            if not predicate(left, right):
                errors.append(f"{name}: violates {operator}={other_name}")


def assess_record(
    record: ExtractedRecord,
    fields: dict[str, Any],
    threshold: float = 0.8,
    *, review_policy: str = "contract",
) -> dict[str, Any]:
    if review_policy not in {"recall", "contract"}:
        raise ValueError("review_policy 必须是 recall 或 contract")
    required = _required_fields(record, fields)
    missing = [name for name in required if _field_missing(record.data, name, fields.get(name))]
    errors: list[str] = []
    present = 0
    for name, raw_rule in fields.items():
        field_name = str(name)
        value = record.data.get(field_name)
        if not _field_missing(record.data, field_name, raw_rule):
            present += 1
        if not isinstance(raw_rule, dict):
            continue
        if raw_rule.get("strict_json"):
            try:
                strict_missing = validate_target_fields(
                    {field_name: value} if field_name in record.data else {}, {field_name: raw_rule},
                )
            except ValueError as exc:
                errors.append(f"{field_name}: {exc}")
            else:
                if field_name not in strict_missing and field_name in missing:
                    missing.remove(field_name)
            if not _missing(value) or raw_rule.get("allow_empty") or raw_rule.get("nullable") is True:
                _compare_fields(record, field_name, value, raw_rule, errors)
            continue
        if _missing(value):
            continue
        rule = raw_rule
        # B06-002：pattern 匹配统一走 safe_regex_search（与 normalizers 对齐），防病态正则自 DOS。
        if rule.get("pattern") and not safe_regex_search(str(rule["pattern"]), str(value)):
            errors.append(f"{field_name}: does not match pattern")
        expected = str(rule.get("type", "string")).casefold()
        if expected in {"int", "integer"}:
            try:
                numeric = _numeric_value(value)
                if numeric != numeric.to_integral_value():
                    raise ValueError("not an integer")
                if rule.get("min") is not None and numeric < _numeric_value(rule["min"]):
                    errors.append(f"{field_name}: below minimum")
                if rule.get("max") is not None and numeric > _numeric_value(rule["max"]):
                    errors.append(f"{field_name}: above maximum")
            except ValueError:
                errors.append(f"{field_name}: is not an integer")
        elif expected in {"float", "number", "money"}:
            try:
                numeric = _numeric_value(value, money=expected == "money")
                if rule.get("min") is not None and numeric < _numeric_value(rule["min"]):
                    errors.append(f"{field_name}: below minimum")
                if rule.get("max") is not None and numeric > _numeric_value(rule["max"]):
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
    evidence_status: dict[str, str] = {}
    evidence_issues: list[str] = []
    from .semantic_changes import values_equal

    for name, raw_rule in fields.items():
        if _field_missing(record.data, str(name), raw_rule):
            continue
        rule = raw_rule if isinstance(raw_rule, dict) else {}
        trace = record.evidence.get(str(name))
        status = "unassessed"
        if isinstance(trace, dict):
            status = "supported" if trace.get("matches", 0) and trace.get("source_url") == record.source_url else "unsupported"
            if trace.get("conflicts"):
                status = "conflict"
            clean = trace.get("clean_value")
            normalizations = record.evidence.get("_normalization", {})
            normalization = normalizations.get(str(name), {}) if isinstance(normalizations, dict) else {}
            normalization = normalization if isinstance(normalization, dict) else {}
            observed = normalization.get("original", record.data.get(str(name)))
            if "clean_value" in trace and not values_equal(clean, observed):
                status = "value_mismatch"
            if rule.get("expected_label") is not None and trace.get("label") != rule["expected_label"]:
                status = "label_mismatch"
        evidence_status[str(name)] = status
        if ((rule.get("evidence_required") or rule.get("critical") or review_policy == "recall")
                and status != "supported") or status in {"conflict", "value_mismatch", "label_mismatch"}:
            evidence_issues.append(f"{name}: evidence {status}")
        if rule.get("min_confidence") is not None:
            confidence_threshold = float(_numeric_value(rule["min_confidence"]))
            if not 0 <= confidence_threshold <= 1:
                raise ValueError("min_confidence 必须在0到1之间")
            confidence = trace.get("confidence") if isinstance(trace, dict) else None
            if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1 or confidence < confidence_threshold):
                evidence_issues.append(f"{name}: source confidence insufficient")
    missing_fields = [str(name) for name in fields if _field_missing(record.data, str(name), fields[name])]
    field_checks = {
        str(name): {
            "presence": "missing" if str(name) in missing_fields else "present",
            "required": str(name) in required,
            "validation_errors": [error for error in errors if error.startswith(f"{name}:")],
            "evidence": evidence_status.get(str(name), "not_applicable"),
            "evidence_issues": [issue for issue in evidence_issues if issue.startswith(f"{name}:")],
        }
        for name in fields
    }
    return {
        "review_policy": review_policy,
        "missing_fields": missing_fields,
        "field_checks": field_checks,
        "score_semantics": "contract_compliance_v1",
        "score": round(score, 4),
        "completeness": round(completeness, 4),
        "missing_required": missing,
        "validation_errors": errors,
        "evidence_status": evidence_status,
        "evidence_issues": evidence_issues,
        "dimensions": {"contract": "failed" if missing or errors else "passed",
                       "evidence": "failed" if evidence_issues else "unassessed" if any(value == "unassessed" for value in evidence_status.values()) else "assessed",
                       "conflicts": "present" if any(value == "conflict" for value in evidence_status.values()) else "none_observed"},
        "review_required": bool(missing or errors or evidence_issues or score < threshold
                                or (review_policy == "recall" and missing_fields)),
    }


def _annotate_anomalies(records: list[ExtractedRecord], fields: dict[str, Any]) -> int:
    anomalies = 0
    for name, rule in fields.items():
        if not isinstance(rule, dict) or not rule.get("anomaly", False):
            continue
        groups: dict[tuple[str, ...], list[tuple[ExtractedRecord, float]]] = {}
        group_fields = rule.get("anomaly_group_by", [])
        if not isinstance(group_fields, list) or any(not isinstance(field, str) for field in group_fields):
            raise ValueError("anomaly_group_by 必须是字段名称列表")
        for record in records:
            try:
                value = float(_numeric_value(record.data.get(str(name), ""), money=rule.get("type") == "money"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            key = tuple(str(record.data.get(str(field), "")) for field in group_fields)
            groups.setdefault(key, []).append((record, value))
        minimum = max(3, int(rule.get("anomaly_min_samples", 5)))
        for numeric in groups.values():
            for record, _value in numeric:
                record.evidence["_quality"].setdefault("anomaly_assessment", {})[str(name)] = "insufficient_samples" if len(numeric) < minimum else "assessed"
            if len(numeric) < minimum:
                continue
            scale = max(abs(value) for _record, value in numeric) or 1.0
            values = [value / scale for _record, value in numeric]
            method = str(rule.get("anomaly_method", "zscore" if "anomaly_zscore" in rule else "mad"))
            if method not in {"zscore", "mad"}:
                raise ValueError("anomaly_method 必须是 zscore 或 mad")
            center = statistics.fmean(values) if method == "zscore" else statistics.median(values)
            deviation = statistics.pstdev(values) if method == "zscore" else statistics.median(abs(value - center) for value in values) * 1.4826
            threshold_z = max(0.1, float(rule.get("anomaly_zscore", 3.0)))
            for record, value in numeric:
                zscore = abs(value / scale - center) / deviation if deviation else None
                if (zscore is not None and zscore <= threshold_z) or (zscore is None and value / scale == center):
                    continue
                quality = record.evidence["_quality"]
                quality.setdefault("anomalies", []).append(
                    {"field": str(name), "value": value, "zscore": round(zscore, 4) if zscore is not None else None,
                     "method": method, "reason": "outlier" if deviation else "differs_from_constant_majority"}
                )
                quality["review_required"] = True
                anomalies += 1
    return anomalies


def assess_records(
    records: list[ExtractedRecord],
    fields: dict[str, Any],
    threshold: float = 0.8,
    unique_by: list[str] | None = None,
    *, review_policy: str = "contract",
) -> dict[str, Any]:
    duplicates = 0
    seen: set[tuple[str, ...]] = set()
    for record in records:
        quality = assess_record(record, fields, threshold, review_policy=review_policy)
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
        present = sum(not _field_missing(record.data, field_name, fields[name]) for record in records)
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

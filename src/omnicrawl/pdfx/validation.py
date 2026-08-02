from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ProjectConfig


@dataclass(slots=True)
class ValidationResult:
    status: str
    messages: list[str]
    review_status: str


def _as_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def validate_record(
    config: ProjectConfig,
    values: dict[str, dict[str, Any]],
    record_confidence: float,
) -> ValidationResult:
    messages: list[str] = []
    invalid = False
    field_map = config.field_map()
    for name, spec in field_map.items():
        value = values.get(name, {})
        raw = value.get("raw_value")
        normalized = value.get("normalized_value")
        if spec.required and not raw:
            messages.append(f"缺少必填字段：{spec.label}")
            invalid = True
        if raw and normalized is None and spec.type in {"amount", "currency", "date", "percent", "integer", "number"}:
            messages.append(f"字段无法标准化：{spec.label}={raw}")
            invalid = True
        if spec.allowed_values and normalized and normalized not in spec.allowed_values:
            messages.append(f"字段不在允许值中：{spec.label}={normalized}")
        number = _as_float(normalized)
        if number is not None and spec.minimum is not None and number < spec.minimum:
            messages.append(f"字段小于下限：{spec.label}={number}")
            invalid = True
        if number is not None and spec.maximum is not None and number > spec.maximum:
            messages.append(f"字段大于上限：{spec.label}={number}")
            invalid = True
        if raw and not value.get("evidence"):
            messages.append(f"字段缺少原文证据：{spec.label}")

    for pair in config.validation.get("required_together", []):
        present = [bool(values.get(name, {}).get("raw_value")) for name in pair]
        if any(present) and not all(present):
            messages.append(f"字段应同时出现：{', '.join(pair)}")

    if invalid:
        status = "invalid"
    elif messages:
        status = "warning"
    else:
        status = "valid"
    threshold = float(config.validation.get("auto_accept_confidence", 0.90))
    review_status = "auto_accepted" if status == "valid" and record_confidence >= threshold else "needs_review"
    return ValidationResult(status, messages, review_status)



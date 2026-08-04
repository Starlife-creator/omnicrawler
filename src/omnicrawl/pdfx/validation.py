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


def _type_format_issues(spec, normalized: str | None) -> list[str]:
    """D29：按字段 type 做合理性校验（年份区间/日期不未来/代码白名单）。"""
    import re
    from datetime import date

    if not normalized:
        return []
    issues: list[str] = []
    spec_type = str(spec.type).casefold()
    if spec_type == "year":
        try:
            year = int(normalized)
            current = date.today().year
            if not 1990 <= year <= current:
                issues.append(f"字段年份超出合理范围：{spec.label}={normalized}")
        except ValueError:
            issues.append(f"字段年份无法解析：{spec.label}={normalized}")
    elif spec_type == "date":
        try:
            from datetime import datetime

            value_str = str(normalized).strip()
            parsed = None
            for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
                try:
                    parsed = datetime.strptime(value_str, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError
            if parsed > date.today():
                issues.append(f"字段日期不可能是未来：{spec.label}={normalized}")
        except ValueError:
            issues.append(f"字段日期无法解析：{spec.label}={normalized}")
    elif spec_type == "code":
        if not re.fullmatch(r"\d{6}", str(normalized)):
            issues.append(f"字段代码不是 6 位数字：{spec.label}={normalized}")
    if spec.value_pattern and not re.fullmatch(spec.value_pattern, str(normalized)):
        issues.append(f"字段取值不符合白名单格式：{spec.label}={normalized}")
    return issues


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
        if raw and normalized is None and spec.type in {"amount", "currency", "date", "percent", "integer", "number", "year", "code"}:
            messages.append(f"字段无法标准化：{spec.label}={raw}")
            invalid = True
        if spec.allowed_values and normalized and normalized not in spec.allowed_values:
            # D30：枚举越界属于数据错误，置 invalid 而非 warning
            messages.append(f"字段不在允许值中：{spec.label}={normalized}")
            invalid = True
        for issue in _type_format_issues(spec, normalized):
            messages.append(issue)
            invalid = True
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

    # D32：跨字段算术勾稽（期初+变动=期末 / 分项合计=总额 / 单笔≤额度）
    for check in config.validation.get("cross_checks", []):
        if not isinstance(check, dict):
            continue
        check_type = str(check.get("type", "")).casefold()
        message = str(check.get("message", "跨字段勾稽失败"))
        if check_type == "sum_equal":
            fields = check.get("fields", [])
            if len(fields) >= 3:
                numbers = [_as_float(values.get(name, {}).get("normalized_value")) for name in fields]
                if all(number is not None for number in numbers):
                    if abs(sum(numbers[:-1]) - numbers[-1]) > 0.005:
                        messages.append(f"跨字段勾稽失败：{message}")
                        invalid = True
        elif check_type == "less_equal":
            low_name = check.get("field")
            high_name = check.get("max_field")
            if low_name and high_name:
                low = _as_float(values.get(low_name, {}).get("normalized_value"))
                high = _as_float(values.get(high_name, {}).get("normalized_value"))
                if low is not None and high is not None and low > high + 0.005:
                    messages.append(f"跨字段勾稽失败：{message}")
                    invalid = True

    if invalid:
        status = "invalid"
    elif messages:
        status = "warning"
    else:
        status = "valid"
    threshold = float(config.validation.get("auto_accept_confidence", 0.90))
    review_status = "auto_accepted" if status == "valid" and record_confidence >= threshold else "needs_review"
    return ValidationResult(status, messages, review_status)



from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .config import ProjectConfig


def _(text: str) -> str:
    """B-P3-8：懒加载 gettext 包裹面向用户文案；翻译资源由 i18n 目录统一补齐。"""
    from ..i18n import _ as __translate

    return __translate(text)

# ── B-P3-2：启发式边界常量（取值依据见注释，便于按业务口径调整）─────────────────────
# 年份下限：近代业务单据/凭证通行下限；更早年份（古早档案）由字段规则自行放宽。
_YEAR_MIN = 1990
# 组织机构/备案代码固定 6 位数字（如统一社会信用代码的局部编号规则）。
_CODE_DIGITS = 6
_CODE_RE = re.compile(rf"\d{{{_CODE_DIGITS}}}")
# 跨字段勾稽绝对容差：金额单位为元时 0.005 元覆盖四舍五入到分的误差；比率类字段不适用。
_CROSS_CHECK_TOLERANCE = 0.005


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
            if not _YEAR_MIN <= year <= current:
                issues.append(_("字段年份超出合理范围：{label}={value}").format(
                    label=spec.label, value=normalized))
        except ValueError:
            issues.append(_("字段年份无法解析：{label}={value}").format(
                label=spec.label, value=normalized))
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
                issues.append(_("字段日期不可能是未来：{label}={value}").format(
                    label=spec.label, value=normalized))
        except ValueError:
            issues.append(_("字段日期无法解析：{label}={value}").format(
                label=spec.label, value=normalized))
    elif spec_type == "code":
        if not _CODE_RE.fullmatch(str(normalized)):
            issues.append(_("字段代码不是 {digits} 位数字：{label}={value}").format(
                digits=_CODE_DIGITS, label=spec.label, value=normalized))
    if spec.value_pattern:
        # S3.2.1：value_pattern 经 safe_regex 编译校验（病态模式拒绝，不卡死）
        from .safe_regex import validate_pattern

        try:
            validate_pattern(spec.value_pattern)
        except ValueError as exc:
            issues.append(_("字段白名单正则无效：{label}={reason}").format(
                label=spec.label, reason=exc))
        else:
            if not re.fullmatch(spec.value_pattern, str(normalized)):
                issues.append(_("字段取值不符合白名单格式：{label}={value}").format(
                    label=spec.label, value=normalized))
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
            messages.append(_("缺少必填字段：{label}").format(label=spec.label))
            invalid = True
        if raw and normalized is None and spec.type in {"amount", "currency", "date", "percent", "integer", "number", "year", "code"}:
            messages.append(_("字段无法标准化：{label}={value}").format(label=spec.label, value=raw))
            invalid = True
        if spec.allowed_values and normalized and normalized not in spec.allowed_values:
            # D30：枚举越界属于数据错误，置 invalid 而非 warning
            messages.append(_("字段不在允许值中：{label}={value}").format(label=spec.label, value=normalized))
            invalid = True
        for issue in _type_format_issues(spec, normalized):
            messages.append(issue)
            invalid = True
        number = _as_float(normalized)
        if number is not None and spec.minimum is not None and number < spec.minimum:
            messages.append(_("字段小于下限：{label}={value}").format(label=spec.label, value=number))
            invalid = True
        if number is not None and spec.maximum is not None and number > spec.maximum:
            messages.append(_("字段大于上限：{label}={value}").format(label=spec.label, value=number))
            invalid = True
        if raw and not value.get("evidence"):
            messages.append(_("字段缺少原文证据：{label}").format(label=spec.label))
        if raw and value.get("source_is_ocr"):
            if value.get("ocr_region", {}).get("adaptive_retry_review"):
                messages.append(_("OCR重试结果不一致或预算受限，需要复核：{label}").format(label=spec.label))
            threshold = float(config.validation.get("auto_accept_confidence", 0.90))
            quality = value.get("source_ocr_confidence")
            if quality is None or not math.isfinite(float(quality)) or not 0 <= float(quality) <= 1:
                messages.append(_("OCR 来源置信度未知，需复核：{label}").format(label=spec.label))
            if float(value.get("confidence", 0.0)) < threshold:
                messages.append(_("OCR 字段置信度不足，需复核：{label}").format(label=spec.label))
            if value.get("extraction_method") == "content_rule":
                other_terms = {
                    term for other in field_map.values() if other.name != name
                    for term in other.search_terms if term
                }
                if any(re.search(rf"{re.escape(term)}\s*[：:]", str(raw)) for term in other_terms):
                    messages.append(_("OCR 字段可能串入其他字段，需复核：{label}").format(label=spec.label))

    for pair in config.validation.get("required_together", []):
        present = [bool(values.get(name, {}).get("raw_value")) for name in pair]
        if any(present) and not all(present):
            messages.append(_("字段应同时出现：{fields}").format(fields=", ".join(pair)))

    # D32：跨字段算术勾稽（期初+变动=期末 / 分项合计=总额 / 单笔≤额度）
    for check in config.validation.get("cross_checks", []):
        if not isinstance(check, dict):
            continue
        check_type = str(check.get("type", "")).casefold()
        message = str(check.get("message", _("跨字段勾稽失败")))
        if check_type == "sum_equal":
            fields = check.get("fields", [])
            if len(fields) >= 3:
                numbers = [_as_float(values.get(name, {}).get("normalized_value")) for name in fields]
                if all(number is not None for number in numbers):
                    checked_numbers = [number for number in numbers if number is not None]
                    if abs(sum(checked_numbers[:-1]) - checked_numbers[-1]) > _CROSS_CHECK_TOLERANCE:
                        messages.append(_("跨字段勾稽失败：{detail}").format(detail=message))
                        invalid = True
        elif check_type == "less_equal":
            low_name = check.get("field")
            high_name = check.get("max_field")
            if low_name and high_name:
                low = _as_float(values.get(low_name, {}).get("normalized_value"))
                high = _as_float(values.get(high_name, {}).get("normalized_value"))
                if low is not None and high is not None and low > high + _CROSS_CHECK_TOLERANCE:
                    messages.append(_("跨字段勾稽失败：{detail}").format(detail=message))
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



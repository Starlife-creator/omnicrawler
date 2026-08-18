"""Safe parsing helpers for untrusted data (LLM responses, HTML, external APIs).

S1.2.5：非法输入返回 None/默认值并记录 warning，不向调用方抛裸异常。
裸 ``json.loads``/``int()``/``float()`` 的替换目标（优先 pipeline / extraction / LLM 响应解析）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

LOGGER = logging.getLogger(__name__)

# S2.5.14 + P9-A4（B05-015）：ReDoS 启发式——命中即拒绝执行。
# CPython 的 _sre 在 C 层执行不释放 GIL，线程超时方案无法抢回执行权，
# 因此执行前直接拒绝（编译+执行均零开销路径不受影响）。
# 检测家族：
# 1. 嵌套量词：(a+)+、(a*)*、(a+){2} —— 组内带量词又被整体量词包裹
_NESTED_QUANTIFIER = re.compile(r"\([^)]*(?:[+*][^)]*)\)[+*{]")
# 2. 叠加量词：a++、a*+、a{1,3}{2} —— 量词后紧跟 * / + / 区间（再次加量）。
#    注意 *? / +? / ?? 的 ? 是合法"非贪婪"后缀，不算叠加，故第二分支不含 ?。
_STACKED_QUANTIFIER = re.compile(r"(?:[+*?]|{[0-9]+(?:,[0-9]*)?})(?:[+*]|{[0-9]+(?:,[0-9]*)?})")
# 3. 大交替重复：(a|b|c|...){n} 且组内 3 个以上分支（指数级回溯面）
_WIDE_ALTERNATION = re.compile(r"\(([^)]*\|[^)]*\|[^)]*)\)\{")


def _unsafe_regex(raw: str) -> bool:
    return bool(
        _NESTED_QUANTIFIER.search(raw)
        or _STACKED_QUANTIFIER.search(raw)
        or _WIDE_ALTERNATION.search(raw)
    )


def safe_regex_search(
    pattern: str | Any,
    text: str,
    *,
    flags: int = 0,
) -> Any:
    """带防护的 re.search；编译错误或可疑模式（嵌套量词）返回 None。

    病态正则不再卡死提取流程——命中启发式即拒绝执行。
    """
    raw = str(pattern)
    try:
        compiled = re.compile(raw, flags)
    except re.error as exc:
        LOGGER.warning("safe_regex_search 编译失败: %r (%s)", raw[:200], exc)
        return None
    if _unsafe_regex(raw):
        LOGGER.warning("safe_regex_search 拒绝可疑模式（ReDoS 启发式）: %r", raw[:200])
        return None
    try:
        return compiled.search(text)
    except re.error:
        return None


def safe_json_loads(
    text: str | bytes | None,
    *,
    default: Any = None,
    log_level: int = logging.WARNING,
) -> Any:
    """JSON 解析失败返回 ``default``，不抛 JSONDecodeError。"""
    if text is None:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        LOGGER.log(log_level, "safe_json_loads 解析失败: %s", exc)
        return default


def safe_int(value: Any, *, default: int | None = None, log_level: int = logging.WARNING) -> int | None:
    """int() 的护栏版本；无法转换返回 ``default``（None 或显式默认）。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        LOGGER.log(log_level, "safe_int 转换失败: %r -> %s", value, exc)
        return default


def safe_float(value: Any, *, default: float | None = None, log_level: int = logging.WARNING) -> float | None:
    """float() 的护栏版本；无法转换返回 ``default``。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        LOGGER.log(log_level, "safe_float 转换失败: %r -> %s", value, exc)
        return default


def safe_get(
    mapping: Any,
    key: str,
    *,
    default: Any = None,
    require_type: type | tuple[type, ...] | None = None,
) -> Any:
    """从可能不是 dict 的值中取键，类型不匹配时返回 ``default``。

    Args:
        mapping: 可能为 None / 非 dict 的来源（如 LLM 返回的 choices[0]）。
        key: 目标键。
        default: 缺失或类型不匹配时的返回值。
        require_type: 若指定，值必须为指定类型（isinstance），否则返回 default。
    """
    if not isinstance(mapping, dict):
        return default
    value = mapping.get(key, default)
    if require_type is not None and not isinstance(value, require_type):
        return default
    return value


def safe_slice(seq: Any, start: int, end: int | None = None) -> list[Any]:
    """对可能为 None / 非序列的值安全切片，返回 list。"""
    if not isinstance(seq, (list, tuple, str, bytes)):
        return []
    try:
        return list(seq[start:end])
    except (TypeError, ValueError, IndexError) as exc:
        LOGGER.log(logging.WARNING, "safe_slice 失败: %s", exc)
        return []


def safe_bool(value: Any, *, default: bool = False) -> bool:
    """bool() 护栏：保留真值语义，但把不可解释的输入转为默认值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off", ""}:
            return False
        return default
    return default

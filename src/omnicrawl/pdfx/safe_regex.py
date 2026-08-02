"""用户自定义正则的长度、复杂度和运行时间保护。"""

from __future__ import annotations

import re
from typing import Any

try:
    import regex as timeout_regex
except ImportError:  # 安装包默认依赖 regex；保留源码直跑兼容性。
    timeout_regex = None

MAX_PATTERN_LENGTH = 4000
MAX_TEXT_LENGTH = 2_000_000
NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)\s*(?:[+*]|\{\d*,?\d*\})")


def validate_pattern(pattern: str) -> None:
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"正则长度超过 {MAX_PATTERN_LENGTH} 字符")
    if NESTED_QUANTIFIER.search(pattern):
        raise ValueError("正则包含高风险嵌套量词")
    (timeout_regex or re).compile(pattern, flags=re.I)


def search(pattern: str, text: str, timeout_seconds: float = 1.0) -> Any:
    validate_pattern(pattern)
    bounded = text[:MAX_TEXT_LENGTH]
    if timeout_regex is not None:
        try:
            return timeout_regex.search(
                pattern, bounded, flags=timeout_regex.I, timeout=timeout_seconds
            )
        except TimeoutError as exc:
            raise ValueError("正则匹配超时") from exc
    return re.search(pattern, bounded, flags=re.I)


def findall_count(pattern: str, text: str, timeout_seconds: float = 1.0) -> int:
    validate_pattern(pattern)
    bounded = text[:MAX_TEXT_LENGTH]
    if timeout_regex is not None:
        try:
            return len(
                timeout_regex.findall(
                    pattern, bounded, flags=timeout_regex.I, timeout=timeout_seconds
                )
            )
        except TimeoutError as exc:
            raise ValueError("正则匹配超时") from exc
    return len(re.findall(pattern, bounded, flags=re.I))

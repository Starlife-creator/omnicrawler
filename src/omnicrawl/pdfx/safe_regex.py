"""用户自定义正则的长度、复杂度和运行时间保护。"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

try:
    import regex as timeout_regex
except ImportError:  # 安装包默认依赖 regex；保留源码直跑兼容性。
    timeout_regex = None

MAX_PATTERN_LENGTH = 4000
MAX_TEXT_LENGTH = 2_000_000
NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)\s*(?:[+*]|\{\d*,?\d*\})")


@lru_cache(maxsize=512)
def _compile_pattern(pattern: str) -> Any:
    """D43：缓存校验+编译结果（retrieval 每页每 pattern 调用，避免数千万次重复编译）。"""
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"正则长度超过 {MAX_PATTERN_LENGTH} 字符")
    if NESTED_QUANTIFIER.search(pattern):
        raise ValueError("正则包含高风险嵌套量词")
    return (timeout_regex or re).compile(pattern, flags=re.I)


def validate_pattern(pattern: str) -> None:
    _compile_pattern(pattern)


def search(pattern: str, text: str, timeout_seconds: float = 1.0) -> Any:
    compiled = _compile_pattern(pattern)
    bounded = text[:MAX_TEXT_LENGTH]
    if timeout_regex is not None:
        try:
            return compiled.search(bounded, timeout=timeout_seconds)
        except TimeoutError as exc:
            raise ValueError("正则匹配超时") from exc
    return compiled.search(bounded)


def findall_count(pattern: str, text: str, timeout_seconds: float = 1.0) -> int:
    compiled = _compile_pattern(pattern)
    bounded = text[:MAX_TEXT_LENGTH]
    if timeout_regex is not None:
        try:
            return len(compiled.findall(bounded, timeout=timeout_seconds))
        except TimeoutError as exc:
            raise ValueError("正则匹配超时") from exc
    return len(compiled.findall(bounded))

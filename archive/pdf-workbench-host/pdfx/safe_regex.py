"""用户自定义正则的长度、复杂度和运行时间保护。"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

try:
    import regex as timeout_regex
except ImportError:  # 安装包默认依赖 regex；保留源码直跑兼容性。
    timeout_regex = None

LOGGER = logging.getLogger(__name__)

MAX_PATTERN_LENGTH = 4000
MAX_TEXT_LENGTH = 2_000_000
NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)\s*(?:[+*]|\{\d*,?\d*\})")

#: B-P2-14a：缺 regex 包回退 stdlib 时，超时保护不可用；告警只发一次避免刷屏。
_degradation_warned = False


def _warn_degradation_once() -> None:
    global _degradation_warned
    if not _degradation_warned:
        LOGGER.warning(
            "未安装可选依赖 'regex'，正则匹配退化为标准库 re：无单条超时保护，"
            "病态正则可能长时间占用 CPU（B-P2-14a 降级告警）。"
        )
        _degradation_warned = True


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
    _warn_degradation_once()
    return compiled.search(bounded)


def findall_count(pattern: str, text: str, timeout_seconds: float = 1.0) -> int:
    compiled = _compile_pattern(pattern)
    bounded = text[:MAX_TEXT_LENGTH]
    if timeout_regex is not None:
        try:
            return len(compiled.findall(bounded, timeout=timeout_seconds))
        except TimeoutError as exc:
            raise ValueError("正则匹配超时") from exc
    _warn_degradation_once()
    return len(compiled.findall(bounded))

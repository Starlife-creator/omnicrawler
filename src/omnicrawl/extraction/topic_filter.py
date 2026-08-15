"""Deterministic topic matching shared by simple and advanced configurations.

S2.5.31：
- 嵌套 dict/list 字段递归参与匹配（tags:["财报"] 可命中）；
- 逐字段独立匹配，不再空格拼接（消除跨字段假命中）；
- filter_records 深拷贝后再写 _topic_match，不污染调用方记录。
"""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TopicDecision:
    matched: bool
    uncertain: bool
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    reason: str


def evaluate_topic(value: Any, config: dict[str, Any]) -> TopicDecision:
    include_any = _terms(config.get("include_any", []))
    include_all = _terms(config.get("include_all", []))
    exclude = _terms(config.get("exclude", []))
    texts = _field_texts(value, config.get("match_on", []))
    excluded = tuple(term for term in exclude if _hit(term, texts))
    any_hits = tuple(term for term in include_any if _hit(term, texts))
    all_hits = tuple(term for term in include_all if _hit(term, texts))
    if excluded:
        return TopicDecision(False, False, any_hits + all_hits, excluded, "命中排除词")
    if include_all and len(all_hits) != len(include_all):
        return TopicDecision(False, not bool(texts), any_hits + all_hits, (), "未命中全部必含词")
    if include_any and not any_hits:
        return TopicDecision(False, not bool(texts), (), (), "未命中任一主题词")
    if not include_any and not include_all:
        return TopicDecision(True, False, (), (), "未设置主题限制")
    return TopicDecision(True, False, any_hits + all_hits, (), "主题匹配")


def filter_records(records: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    if not config.get("enabled", False):
        return records
    keep_uncertain = bool(config.get("keep_uncertain", True))
    filtered: list[dict[str, Any]] = []
    for record in records:
        decision = evaluate_topic(record, config)
        # S2.5.31：深拷贝后再写元数据，不污染调用方 record dict
        record = copy.deepcopy(record)
        record["_topic_match"] = {
            "matched": decision.matched,
            "uncertain": decision.uncertain,
            "included": list(decision.included),
            "excluded": list(decision.excluded),
            "reason": decision.reason,
        }
        if decision.matched or (decision.uncertain and keep_uncertain):
            filtered.append(record)
    return filtered


def _terms(value: Any) -> tuple[str, ...]:
    # S4.5 P3#152：配置解析结果缓存——每条记录不再重复归一化词表。
    # B07-003：改用 functools.lru_cache（maxsize=256）限界，长运行服务下
    # 词表组合频繁变化时缓存不无限增长。
    if not isinstance(value, list):
        return ()
    return _terms_cached(tuple(value))


@functools.lru_cache(maxsize=256)
def _terms_cached(key: tuple) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(str(item).strip().casefold() for item in key if str(item).strip())
    )


def _hit(term: str, texts: tuple[str, ...]) -> bool:
    return any(term in text for text in texts)


def _field_texts(value: Any, match_on: Any) -> tuple[str, ...]:
    """按顶层字段分组收集文本：嵌套 dict/list 递归展开并入其字段文本，
    字段之间彼此独立（不拼接），term 只在其所属字段内匹配。"""
    fields = {str(item) for item in match_on} if isinstance(match_on, list) else set()
    parts: dict[str, list[str]] = {}

    def _walk(item: Any, key: str) -> None:
        if isinstance(item, dict):
            for child_key, child in item.items():
                if str(child_key).startswith("_"):
                    continue
                _walk(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                _walk(child, key)
        elif item is not None:
            parts.setdefault(key, []).append(str(item))

    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            if fields and str(key) not in fields:
                continue
            _walk(item, str(key))
    elif value is not None:
        parts.setdefault("", []).append(str(value))
    return tuple(" ".join(chunk).casefold() for chunk in parts.values())

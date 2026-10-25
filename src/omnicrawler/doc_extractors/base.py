"""文档槽位抽取基础（批 C-1）。

- ``SlotHit``：一次槽位抽取结果（值 + 置信度 + 证据）。
- ``TextDocExtractor``：文本/正则槽位（safe_regex_search 防病态正则）。
- ``JSONDocExtractor``：jsonpath 槽位（复用 core.jsonpath 引擎）+ 正则兜底。

槽位定义见 ``state.scene_store.SlotDefinition``，extractor_type 取值：
css（HTML）、regex（文本/PDF/HTML）、jsonpath（JSON）、text（包含匹配）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..core.safe_data import safe_regex_search

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SlotHit:
    """一次槽位抽取结果。"""

    slot_key: str
    value: Any
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)


def _error_hit(definition: Any, exc: Exception) -> SlotHit:
    """B-P2-14e：定义级异常隔离，产出带错误类型与消息的错误槽位，不中断其余定义。"""
    return SlotHit(
        definition.slot_key,
        None,
        0.0,
        {"error_type": type(exc).__name__, "error": str(exc), "matches": 0},
    )


def _regex_value(pattern: str, text: str) -> tuple[Any, float, dict[str, Any]]:
    """正则槽位：捕获组 1 优先；未命中返回 (None, 0.0, 空证据)。"""
    match = safe_regex_search(pattern, text)
    if not match:
        return None, 0.0, {"pattern": pattern, "matches": 0}
    if match.lastindex:
        return match.group(1), 1.0, {"pattern": pattern, "matches": 1}
    return match.group(0), 1.0, {"pattern": pattern, "matches": 1}


class TextDocExtractor:
    """文本文档抽取器：regex / text 槽位。"""

    def extract(self, text: str, definitions: list[Any]) -> list[SlotHit]:
        hits: list[SlotHit] = []
        for definition in definitions:
            try:
                if definition.extractor_type == "regex":
                    value, confidence, evidence = _regex_value(definition.pattern, text)
                    if value is not None:
                        hits.append(SlotHit(definition.slot_key, value, confidence, evidence))
                elif definition.extractor_type == "text":
                    if definition.pattern and definition.pattern in text:
                        hits.append(SlotHit(
                            definition.slot_key, definition.pattern, 1.0,
                            {"pattern": definition.pattern, "matches": 1},
                        ))
            except Exception as exc:  # noqa: BLE001 - 定义级隔离，坏正则不中断其余槽位
                LOGGER.warning("文本槽位 %s 提取失败，记录错误槽位: %s: %s",
                               definition.slot_key, type(exc).__name__, exc)
                hits.append(_error_hit(definition, exc))
        return hits


class JSONDocExtractor:
    """JSON 文档抽取器：jsonpath 槽位（core.jsonpath 引擎）+ 正则兜底。"""

    def extract(self, payload: Any, definitions: list[Any]) -> list[SlotHit]:
        from ..core.jsonpath import json_path

        hits: list[SlotHit] = []
        text_repr = json.dumps(payload, ensure_ascii=False, default=str)
        for definition in definitions:
            try:
                if definition.extractor_type == "jsonpath":
                    values = json_path(payload, definition.pattern)
                    if values:
                        hits.append(SlotHit(
                            definition.slot_key, values[0], 1.0,
                            {"jsonpath": definition.pattern, "matches": len(values)},
                        ))
                elif definition.extractor_type == "regex":
                    value, confidence, evidence = _regex_value(definition.pattern, text_repr)
                    if value is not None:
                        hits.append(SlotHit(definition.slot_key, value, confidence, evidence))
            except Exception as exc:  # noqa: BLE001 - 定义级隔离
                LOGGER.warning("JSON 槽位 %s 提取失败，记录错误槽位: %s: %s",
                               definition.slot_key, type(exc).__name__, exc)
                hits.append(_error_hit(definition, exc))
        return hits


__all__ = ["JSONDocExtractor", "SlotHit", "TextDocExtractor", "_regex_value"]

"""Dependency-free OCR result contract shared by native and component backends."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OCRRichResult:
    text: str
    confidence: float | None
    words: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def structure_json(self, *, dpi: int) -> str:
        return json.dumps({"words": self.words, "blocks": self.blocks, "tables": self.tables,
                           "metadata": {**self.metadata, "dpi": dpi}}, ensure_ascii=False, allow_nan=False)


def field_region(raw: str, structure: dict[str, Any]) -> dict[str, Any]:
    """Conservative confidence of all OCR regions covering the exact observed value."""
    words = structure.get("words", [])
    target = re.sub(r"\s+", "", raw)
    if not target or not isinstance(words, list) or len(words) > 100000:
        return {"status": "unmapped", "confidence": None}
    parts = [re.sub(r"\s+", "", str(word.get("text", ""))) for word in words if isinstance(word, dict)]
    if len(parts) != len(words):
        return {"status": "invalid_structure", "confidence": None}
    text = "".join(parts)
    ends = []
    offset = 0
    for part in parts:
        offset += len(part)
        ends.append(offset)
    matches = []
    start = text.find(target)
    while start >= 0:
        indexes = [i for i, end in enumerate(ends) if end > start and end - len(parts[i]) < start + len(target)]
        scores = [words[i].get("confidence") for i in indexes]
        known = bool(scores) and all(type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1 for score in scores)
        matches.append({"word_indexes": indexes, "confidence": min(scores) if known else None,
                        "boxes": [words[i].get("bbox") for i in indexes]})
        if len(matches) > 100:
            return {"status": "ambiguous", "confidence": None}
        start = text.find(target, start + 1)
    metadata = structure.get("metadata", {})
    retry = metadata.get("adaptive_retry", {})
    retry = retry if isinstance(retry, dict) else {}
    return {"status": "matched" if len(matches) == 1 else "ambiguous" if matches else "unmapped",
            "confidence": matches[0]["confidence"] if len(matches) == 1 else None,
            "matches": matches, "coordinate_system": metadata.get("coordinate_system", "unknown"),
            "original_mapping": metadata.get("original_mapping", "unknown"), "dpi": metadata.get("dpi"),
            "adaptive_retry_review": bool(retry.get("review_required"))}


def text_quality(text: str) -> tuple[int, float]:
    printable = sum(1 for char in text if char.isprintable() and not char.isspace())
    if not text:
        return 0, 1.0
    bad = text.count("\ufffd") + text.count("\x00")
    control = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    ratio = min(1.0, (bad + control) / max(1, len(text)))
    return printable, ratio

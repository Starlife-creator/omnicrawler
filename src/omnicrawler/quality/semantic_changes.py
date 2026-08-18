from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True, slots=True)
class SemanticChange:
    change_type: str
    identity: str
    similarity: float
    added_fields: tuple[str, ...] = ()
    removed_fields: tuple[str, ...] = ()
    modified_fields: tuple[str, ...] = ()
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    return value


def record_identity(data: dict[str, Any], source_url: str = "") -> str:
    for key in ("id", "identifier", "uuid", "doi", "url", "link", "title", "name"):
        value = data.get(key)
        if value not in (None, "", []):
            return f"{key}:{normalize_value(value)}"
    stable = json.dumps(normalize_value(data), ensure_ascii=False, sort_keys=True, default=str)
    return "hash:" + hashlib.sha256(f"{source_url}|{stable}".encode()).hexdigest()[:24]


def semantic_hash(data: dict[str, Any], *, ignored_fields: set[str] | None = None) -> str:
    ignored = ignored_fields or {"fetched_at", "updated_at", "crawl_time", "timestamp"}
    cleaned = {key: value for key, value in data.items() if key.casefold() not in ignored}
    payload = json.dumps(normalize_value(cleaned), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compare_record_data(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    identity: str = "",
    ignored_fields: set[str] | None = None,
) -> SemanticChange:
    if before is None and after is not None:
        return SemanticChange("added", identity, 0.0, added_fields=tuple(sorted(after)), after=after)
    if before is not None and after is None:
        return SemanticChange("removed", identity, 0.0, removed_fields=tuple(sorted(before)), before=before)
    before = before or {}
    after = after or {}
    ignored = ignored_fields or {"fetched_at", "updated_at", "crawl_time", "timestamp"}
    before_keys = {key for key in before if key.casefold() not in ignored}
    after_keys = {key for key in after if key.casefold() not in ignored}
    added = tuple(sorted(after_keys - before_keys))
    removed = tuple(sorted(before_keys - after_keys))
    modified = tuple(
        sorted(
            key for key in before_keys & after_keys
            if normalize_value(before[key]) != normalize_value(after[key])
        )
    )
    left = json.dumps(normalize_value(before), ensure_ascii=False, sort_keys=True, default=str)
    right = json.dumps(normalize_value(after), ensure_ascii=False, sort_keys=True, default=str)
    similarity = round(SequenceMatcher(None, left, right).ratio(), 4)
    change_type = "unchanged" if not (added or removed or modified) else "modified"
    return SemanticChange(change_type, identity, similarity, added, removed, modified, before, after)

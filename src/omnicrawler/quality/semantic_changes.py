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
    #: 该变化是否属于**首个同步周期的基线**（首轮把初始记录记为 added 时置 True）。
    #: 它是**事实**：数据层只如实标注；是否据此提示用户由 UI / 通知层决定
    #: （见 quality_report.notifiable_changes）。放在字段末尾以免既有位置参数构造错位。
    baseline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def entity_checkpoint_key(record_type: str, identity: str) -> str:
    """Address a presence tombstone without scanning checkpoint payloads."""
    return "entity:" + hashlib.sha256(json.dumps([record_type, identity], ensure_ascii=False).encode()).hexdigest()


def normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    return value


def _equal_normalized(left: Any, right: Any) -> bool:
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal_normalized(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal_normalized(one, two) for one, two in zip(left, right, strict=True))
    return left == right


def values_equal(left: Any, right: Any) -> bool:
    """Normalize text while preserving boolean types throughout nested values."""
    return _equal_normalized(normalize_value(left), normalize_value(right))


def record_identity(data: dict[str, Any], source_url: str = "", *, identity_fields: tuple[str, ...] = ()) -> str:
    if identity_fields:
        if any(data.get(key) in (None, "", []) for key in identity_fields):
            raise ValueError("显式业务主键缺失；不能退化为标题或内容身份")
        stable = json.dumps([(key, normalize_value(data[key])) for key in identity_fields], ensure_ascii=False, default=str)
        return "key:" + hashlib.sha256(stable.encode()).hexdigest()
    # 中英文等价键都要认：本项目分析器产出的字段名是中文（标题/编号/链接地址/名称），
    # 而这里原先只认英文键 ⇒ 中文配置下退化成"按内容哈希取身份"，
    # 于是**任何字段变化（如改价）都会被判成「删除+新增」而不是「修改」**，
    # 变更语义静默失真（实测：{"标题":"甲","价格":"1"} → 改价后身份从 hash:A 变 hash:B）。
    # 中文键插在对应英文键之后，保证英文配置的身份取值顺序不变。
    for key in (
        "id", "identifier", "uuid", "doi", "编号",
        "url", "link", "链接地址",
        "title", "标题", "name", "名称",
    ):
        value = data.get(key)
        if value not in (None, "", []):
            return f"{key}:{normalize_value(value)}"
    stable = json.dumps(normalize_value(data), ensure_ascii=False, sort_keys=True, default=str)
    return "hash:" + hashlib.sha256(f"{source_url}|{stable}".encode()).hexdigest()[:24]


def semantic_hash(data: dict[str, Any], *, ignored_fields: set[str] | None = None) -> str:
    ignored = {"fetched_at", "updated_at", "crawl_time", "timestamp"} if ignored_fields is None else {key.casefold() for key in ignored_fields}
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
    ignored = {"fetched_at", "updated_at", "crawl_time", "timestamp"} if ignored_fields is None else {key.casefold() for key in ignored_fields}
    before_keys = {key for key in before if key.casefold() not in ignored}
    after_keys = {key for key in after if key.casefold() not in ignored}
    added = tuple(sorted(after_keys - before_keys))
    removed = tuple(sorted(before_keys - after_keys))
    modified = tuple(
        sorted(
            key for key in before_keys & after_keys
            if not values_equal(before[key], after[key])
        )
    )
    left = json.dumps(normalize_value(before), ensure_ascii=False, sort_keys=True, default=str)
    right = json.dumps(normalize_value(after), ensure_ascii=False, sort_keys=True, default=str)
    similarity = round(SequenceMatcher(None, left, right).ratio(), 4)
    change_type = "unchanged" if not (added or removed or modified) else "modified"
    return SemanticChange(change_type, identity, similarity, added, removed, modified, before, after)

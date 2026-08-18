"""P2-1：结构指纹签名 — 借鉴 Scrapling 元素结构指纹思想。

合规边界声明：
    本模块**仅对已抓取结果的 data 字段做结构签名**（键集合 + 值类型 + 嵌套层级），
    不含任何值内容，不用于请求侧指纹识别或反检测。
    用途：
      1. 模板去重 — 同一站点模板产出的多条记录结构签名相同，可在 metrics 聚合
      2. 模板匹配建议 — 新记录的结构签名与已有模板签名比对，辅助推荐配置
      3. 结构漂移检测 — 同一 source_url 的记录结构签名变化 → 可能模板已变更

与现有 fingerprint 的区别：
    - CrawlRequest.fingerprint  = 请求级（URL + headers + body）
    - FetchResult.content_hash  = 响应体字节级 sha256
    - semantic_hash(data)       = 记录值级 sha256（数据变了就变）
    - structure_fingerprint(data) = 记录结构级（键集合 + 类型签名；同模板不同数据 → 相同）

签名格式：
    "v1:{record_type}:{type_tree_hash}"
    其中 type_tree_hash = sha256(规范化类型树的 JSON 序列化) 前 16 hex 字符。

类型树示例：
    {"name": "张三", "age": 30, "tags": ["a","b"], "meta": {"src": "x"}}
    →
    {"name": "str", "age": "int", "tags": ["str"], "meta": {"src": "str"}}
    → 类型树 JSON（键排序）→ sha256 前 16 字符
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

__all__ = [
    "SCHEMA_VERSION",
    "structure_fingerprint",
    "type_signature",
    "StructureFingerprintRegistry",
]

SCHEMA_VERSION = "v1"


def type_signature(value: Any) -> str:
    """把任意 Python 值映射为结构类型签名字符串。

    规则：
    - None → "null"
    - bool → "bool"（bool 是 int 子类，必须先判 bool）
    - int → "int"
    - float → "float"
    - str → "str"
    - list/tuple → 递归元素签名，如 ``[str]``；空列表 → ``[]``
    - dict → 递归键值签名，如 ``{name:str, age:int}``；空 dict → ``{}``
    - 其他 → 类型名（``type(value).__name__``）
    """
    if value is None:
        return "null"
    # 注意：bool 是 int 的子类，必须先判
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        # 取第一个元素的类型签名（列表通常同构；异构时取首元素是合理近似）
        return f"[{type_signature(value[0])}]"
    if isinstance(value, Mapping):
        if not value:
            return "{}"
        parts = []
        for key in sorted(str(k) for k in value):
            inner = value.get(key) if isinstance(value, dict) else value[str(key)]
            parts.append(f"{key}:{type_signature(inner)}")
        return "{" + ",".join(parts) + "}"
    return type(value).__name__


def _build_type_tree(data: Mapping[str, Any]) -> dict[str, Any]:
    """把 record.data 转成类型树（递归 type_signature）。

    顶层一定是 dict；值可能是嵌套 dict / list / 标量。
    """
    tree: dict[str, Any] = {}
    for key in data:
        tree[str(key)] = type_signature(data[key])
    return tree


def structure_fingerprint(data: Mapping[str, Any], *, record_type: str = "") -> str:
    """计算一条 ExtractedRecord.data 的结构指纹。

    Parameters
    ----------
    data:
        ExtractedRecord.data（键值映射）。
    record_type:
        可选的记录类型标签（如 ``html_item`` / ``json_item``）；
        不同 record_type 即使 data 结构相同也会产生不同指纹，
        因为它们语义上是不同模板。

    Returns
    -------
    str
        ``"v1:{record_type}:{16hex}"`` 格式的结构指纹；
        空 data 返回 ``"v1:{record_type}:empty"``。
    """
    if not data:
        return f"{SCHEMA_VERSION}:{record_type or 'unknown'}:empty"
    tree = _build_type_tree(data)
    # 规范化 JSON：键排序、ensure_ascii=False、无空格
    payload = json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{SCHEMA_VERSION}:{record_type or 'unknown'}:{digest}"


class StructureFingerprintRegistry:
    """结构指纹注册表：跟踪已见签名 + 计数 + 反向查 source_url。

    线程安全；用于 metrics 聚合（"这个 run 里有几种结构模板"）和
    结构漂移检测（"这个 source_url 的记录结构签名变了"）。
    """

    __slots__ = ("_signatures", "_url_to_signatures", "_lock")

    def __init__(self) -> None:
        # signature → 出现次数
        self._signatures: dict[str, int] = {}
        # source_url → 该 URL 出现过的签名集合
        self._url_to_signatures: dict[str, set[str]] = {}
        import threading

        self._lock = threading.Lock()

    def observe(self, signature: str, source_url: str = "") -> bool:
        """记录一条签名。返回 True 表示这是该 source_url 的新结构（漂移信号）。

        Parameters
        ----------
        signature:
            :func:`structure_fingerprint` 返回值。
        source_url:
            可选；用于跟踪同一 URL 的结构漂移。
        """
        is_drift = False
        with self._lock:
            self._signatures[signature] = self._signatures.get(signature, 0) + 1
            if source_url:
                seen = self._url_to_signatures.get(source_url)
                if seen is None:
                    self._url_to_signatures[source_url] = {signature}
                else:
                    if signature not in seen:
                        seen.add(signature)
                        is_drift = True
        return is_drift

    def count(self, signature: str) -> int:
        with self._lock:
            return self._signatures.get(signature, 0)

    def unique_count(self) -> int:
        with self._lock:
            return len(self._signatures)

    def top_signatures(self, limit: int = 10) -> list[tuple[str, int]]:
        """返回出现次数最多的 (signature, count) 对，按计数降序。"""
        with self._lock:
            items = sorted(self._signatures.items(), key=lambda kv: (-kv[1], kv[0]))
            return items[:limit]

    def signatures_for_url(self, source_url: str) -> frozenset[str]:
        """返回某 source_url 出现过的所有签名（用于漂移分析）。"""
        with self._lock:
            return frozenset(self._url_to_signatures.get(source_url, set()))

    def clear(self) -> None:
        with self._lock:
            self._signatures.clear()
            self._url_to_signatures.clear()

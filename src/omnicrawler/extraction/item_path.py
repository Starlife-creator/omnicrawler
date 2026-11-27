"""JSON 「记录在哪」的判据：候选打分（走查 R4.2）。

## 背景（0.13.0 端到端走查，`.audit-tmp/w71/cases/c26`、`c47`、`c48`）

三个**单对象** API 响应都被选中了对象内部的**无关数组**：

| 用例 | URL | 旧实现选中 | 实际产出 | 本该是 |
|---|---|---|---|---|
| c26 | `pokeapi.co/api/v2/type/1` | `$.game_indices[*]` | 9 条 `{game_index}` | 「这个属性」本身 |
| c47 | `dummyjson.com/products/1` | `$.reviews[*]` | 3 条评论 | 「这个商品」本身 |
| c48 | `pokeapi.co/api/v2/pokemon/1` | `$.abilities[*]` | 2 条 `{is_hidden, slot}` | 「这只宝可梦」本身 |

三例机制相同 ⇒ **系统性**缺陷；而质量报告里的 completeness 都是 1.0 ——
**静默地给了用户错的东西**，比报错更坏。

旧判据是「字典里第一个元素为对象的数组」：只看"是不是数组"，不看它有多像"记录集合"，
更不考虑"这份响应本身可能就是一条记录"。

## 一处实现，两个消费方

`intelligent_scraper`（自动配置）与 `api_discovery`（浏览器 XHR → REST 模板）都要回答
同一个问题，此前各写一份、各错各的；后者还把路径写成 `results`（**缺 `[*]`**）——
实测 `json_path(payload, "results")` 返回 `[[全部元素]]`，即**一条**记录。
⇒ 判据统一到这里。本模块**不依赖抽取器**：它只产出 JSONPath 字符串与对应取值，
"这两者是否一致"由守卫测试用真实求值器 `json_path` 反查（见
`tests/unit/extraction/test_json_item_path_r42.py`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: 集合语义键：这些名字几乎总是「记录容器」，而不是「某条记录内部的一个数组」。
_COLLECTION_KEYS = frozenset({
    "items", "item", "results", "result", "data", "records", "record", "rows", "row",
    "list", "lists", "entries", "entry", "elements", "objects", "nodes", "children",
    "hits", "documents", "docs", "values", "products", "articles", "posts", "users",
    "orders", "comments", "messages", "events", "tasks", "files", "feeds",
})

#: 标识类字段名：元素的「身份」。带这些键的数组更像**主记录集合**。
_IDENTIFIER_KEYS = frozenset({
    "id", "uuid", "guid", "sku", "code", "key", "slug", "name", "title", "url", "link", "path",
})

#: 打分权重。★ 改动它们会改变 c26/c47/c48 三个真实用例的排序 ——
#: 改权重前先跑 `tests/unit/extraction/test_json_item_path_r42.py`。
_W_SIZE = 2.0            # 数组规模
_W_FIELDS = 3.0          # 元素字段数（对象）/ 标量字段数（单对象）
_W_IDENT = 2.5           # 含标识字段
_W_SEMANTIC = 2.5        # 键名是集合语义
_W_SINGLE_RECORD = 2.5   # URL 以资源 id 收尾 ⇒ 单对象旁证
_W_URL_MATCH = 2.0       # 键名与 URL 路径词吻合

_SIZE_CAP = 50           # 数组规模封顶（再长也不加分，避免"最大者恒胜"）
_FIELDS_CAP = 12         # 字段数封顶

#: 记录路径的嵌套深度上限：深度 0 = `$.a[*]`，深度 1 = `$.a.b[*]`。
#: 走查 R4.2 的「1–2 层」读作：既铺平字段，也**允许 2 层键**的记录路径。
_MAX_ARRAY_DEPTH = 2

#: URL 里不参与「路径词」比较的通用段。
_URL_NOISE = frozenset({
    "api", "apis", "rest", "json", "graphql", "www", "index", "default", "public",
    "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9",
})

#: JSONPath 求值器（`extractors.json_path`）按 `.` 与 `[]` 切分，因此键名里
#: 含这些字符时**无法表达**。这类键必须显式跳过并报出来（不静默写一个错路径）。
_UNREPRESENTABLE = re.compile(r"[.\[\]]")


@dataclass(frozen=True, slots=True)
class ItemPathCandidate:
    """一个候选记录路径，附**可解释**的得分。"""

    path: str                 # JSONPath，如 `$.results[*]` / `$` / `$[*]`
    values: list[Any]         # 该路径取到的记录（与 `json_path(payload, path)` 一致）
    score: float
    reasons: tuple[str, ...]

    @property
    def sample(self) -> Any:
        return self.values[0] if self.values else None

    @property
    def item_count(self) -> int:
        return len(self.values)


def is_representable(key: Any) -> bool:
    """键名能否用现有 JSONPath 表达（不能就得显式跳过并报出来）。"""
    text = str(key)
    return bool(text) and not _UNREPRESENTABLE.search(text)


def unrepresentable_path_keys(sample: Any, *, depth: int = 0) -> tuple[str, ...]:
    """列出样本里**无法表达为 JSONPath** 的键（路径风格），用于如实告知。"""
    found: list[str] = []
    if not isinstance(sample, dict) or depth >= _MAX_ARRAY_DEPTH:
        return ()
    for key, value in sample.items():
        if isinstance(key, str) and not is_representable(key):
            found.append(str(key))
            continue
        if isinstance(value, dict):
            found.extend(f"{key}.{child}" for child in unrepresentable_path_keys(value, depth=depth + 1))
        elif isinstance(value, list):
            for item in value[:5]:
                if isinstance(item, dict):
                    found.extend(
                        f"{key}[*].{child}" for child in unrepresentable_path_keys(item, depth=depth + 1)
                    )
    return tuple(found)


def _url_words(url: str) -> frozenset[str]:
    """URL 路径里「有意义的词」（去掉 api / v2 这类通用段与纯数字段）。"""
    if not url:
        return frozenset()
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    words = re.split(r"[/\-_.]+", path)
    return frozenset(
        word.casefold()
        for word in words
        if len(word) > 1 and not word.isdigit() and word.casefold() not in _URL_NOISE
    )


def _url_ends_with_id(url: str) -> bool:
    """URL 是否以「单个资源标识」收尾（`/type/1`、`/users/ada`）。

    ★ 这是「单对象响应」最有力的旁证：REST 的单资源地址通常以 id 收尾。
    """
    if not url:
        return False
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    tail = path.rsplit("/", 1)[-1]
    if not tail:
        return False
    if tail.isdigit():
        return True
    return bool(re.fullmatch(r"[0-9a-f]{8,}(?:-[0-9a-f]+)*", tail, flags=re.IGNORECASE))


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 2 and word.endswith("s") else word


def _word_matches_key(words: frozenset[str], key: str) -> bool:
    if not words:
        return False
    target = _singular(key.casefold())
    return any(_singular(word) == target for word in words)


def _scalar_count(mapping: dict[str, Any]) -> int:
    return sum(1 for value in mapping.values() if not isinstance(value, (dict, list)))


def _has_identifier(mapping: dict[str, Any]) -> bool:
    return any(str(key).casefold() in _IDENTIFIER_KEYS for key in mapping)


def _segment(key: str) -> str | None:
    return f".{key}" if is_representable(key) else None


def _array_candidate(
    path: str, key: str, values: list[Any], words: frozenset[str]
) -> ItemPathCandidate:
    dicts = [item for item in values[:_SIZE_CAP] if isinstance(item, dict)]
    score = min(len(values), _SIZE_CAP) / _SIZE_CAP * _W_SIZE
    reasons = [f"数组 {len(values)} 项"]

    field_count = max((_scalar_count(item) for item in dicts), default=0)
    if field_count:
        score += min(field_count, _FIELDS_CAP) / _FIELDS_CAP * _W_FIELDS
        reasons.append(f"元素最多 {field_count} 个标量字段")
    if dicts and _has_identifier(dicts[0]):
        score += _W_IDENT
        reasons.append("元素含标识字段")
    if key.casefold() in _COLLECTION_KEYS:
        score += _W_SEMANTIC
        reasons.append("键名是集合语义")
    if _word_matches_key(words, key):
        score += _W_URL_MATCH
        reasons.append("键名与 URL 路径词吻合")

    return ItemPathCandidate(path, values, round(score, 3), tuple(reasons))


def _shared_prefix_records(value: list[Any]) -> list[dict[str, Any]] | None:
    """识别 **Nuxt 2 式共享前缀**列表并合并成记录；不是该形态返回 None。

    实测形态（Nuxt 2 的 payload 序列化）：

        data = [
          [{"id": 1, "brand": "Acme"}, {"id": 2, "brand": "Acme"}],   <- 公共前缀
          {"name": "Alpha", "price": 9.9},                            <- 第 1 条
          {"name": "Beta",  "price": 19.9},                           <- 第 2 条
        ]

    分开看：`data[*]` 里只有两个 dict（外加一个 list），`data[0][*]` 只有 ID，
    两处都"不像记录集合"，于是整页被判成"没有可用数据" —— 而记录其实都在。

    ★ 判据必须包含「**共享前缀与记录互补**」这一条：只看形状的话，
      `[[{a:1}], {a:2, name:"x"}]` 这种普通嵌套也会被误拼成一条假记录。
    """
    if len(value) < 2 or not isinstance(value[0], list):
        return None
    head, rows = value[0], value[1:]
    if not rows or not all(isinstance(row, dict) for row in rows):
        return None
    if head and not all(isinstance(item, dict) for item in head):
        return None
    shared = set(head[0]) if head else set()
    if shared and shared <= set(rows[0]):
        # 公共前缀的键在记录里**全都有** ⇒ 它不是被抽出去的共享值，只是普通嵌套数组
        return None
    merged: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        base = dict(head[index % len(head)]) if head else {}
        base.update(row)
        merged.append(base)
    return merged


def _dict_candidates(
    prefix: str,
    mapping: dict[str, Any],
    words: frozenset[str],
    depth: int,
    *,
    shared_prefix: bool = False,
    budget: list[int] | None = None,
    max_depth: int = _MAX_ARRAY_DEPTH,
) -> list[ItemPathCandidate]:
    """把一个 dict 里"可能是记录集合"的嵌套数组都变成候选。

    ``shared_prefix`` 是给**内嵌 SSR 数据**用的（见 ``embedded_json``）：那些框架会
    把公共字段抽到第一个元素里做去重，于是同一份列表被拆成"只有 ID 的公共前缀"
    与"只有业务字段的剩余行"。分开看全是残片，合起来才是完整记录。

    ★ 刻意**不**默认开启：普通 REST 响应里的嵌套数组是**各自独立的数据**，
      硬拼会给出一条字段对不上的假记录。

    ``budget`` 是遍历配额（单元素列表，跨递归共享）：内嵌载荷可能有成千上万个
    嵌套对象，深度优先全扫一遍既慢又没必要 —— 真正的记录集合几乎总在浅层。
    """
    out: list[ItemPathCandidate] = []
    for key, value in mapping.items():
        if not isinstance(value, list) or not value:
            continue
        segment = _segment(str(key))
        if segment is None:
            continue
        out.append(_array_candidate(f"{prefix}{segment}[*]", str(key), value, words))
        if shared_prefix:
            merged = _shared_prefix_records(value)
            if merged:
                out.append(
                    _array_candidate(f"{prefix}{segment}[*]", str(key), merged, words)
                )
    if depth + 1 < max_depth:
        for key, value in mapping.items():
            if not isinstance(value, dict) or not value:
                continue
            segment = _segment(str(key))
            if segment is None:
                continue
            if budget is not None:
                if budget[0] <= 0:
                    return out
                budget[0] -= 1
            out.extend(
                _dict_candidates(
                    f"{prefix}{segment}",
                    value,
                    words,
                    depth + 1,
                    shared_prefix=shared_prefix,
                    budget=budget,
                    max_depth=max_depth,
                )
            )
    return out


def rank_item_paths(
    payload: Any,
    *,
    url: str = "",
    shared_prefix: bool = False,
    budget: int | None = None,
    max_depth: int = _MAX_ARRAY_DEPTH,
) -> tuple[ItemPathCandidate, ...]:
    """给「记录路径」的所有候选打分，按分数降序（同分按路径字典序）返回。

    候选来自两处：
    - **对象本身作为一条记录**（`$`）—— 单对象响应；得分看标量字段数、标识字段、
      以及 URL 是否以资源 id 收尾；
    - **嵌套数组**（`$.a[*]` / `$.a.b[*]`）—— 得分看数组规模、元素字段数、标识字段、
      键名是否集合语义、键名是否与 URL 路径词吻合。

    ``shared_prefix`` 见 ``_dict_candidates``；``budget`` 限制嵌套遍历次数。
    """
    words = _url_words(url)
    candidates: list[ItemPathCandidate] = []
    limit = [budget] if budget is not None else None

    if isinstance(payload, list):
        if payload:
            candidates.append(_array_candidate("$[*]", "", payload, words))
            if shared_prefix:
                merged = _shared_prefix_records(payload)
                if merged:
                    candidates.append(_array_candidate("$[*]", "", merged, words))
    elif isinstance(payload, dict):
        scalar_count = _scalar_count(payload)
        if scalar_count:
            score = min(scalar_count, _FIELDS_CAP) / _FIELDS_CAP * _W_FIELDS
            reasons = [f"{scalar_count} 个标量字段"]
            if _has_identifier(payload):
                score += _W_IDENT
                reasons.append("含标识字段")
            if _url_ends_with_id(url):
                score += _W_SINGLE_RECORD
                reasons.append("URL 以资源 id 收尾（单对象）")
            candidates.append(ItemPathCandidate("$", [payload], round(score, 3), tuple(reasons)))
        candidates.extend(
            _dict_candidates(
                "$",
                payload,
                words,
                0,
                shared_prefix=shared_prefix,
                budget=limit,
                max_depth=max_depth,
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.path))
    return tuple(candidates)


def choose_item_path(
    payload: Any, *, url: str = "", shared_prefix: bool = False
) -> ItemPathCandidate | None:
    """取分数最高的候选；没有任何候选时返回 ``None``。"""
    ranked = rank_item_paths(payload, url=url, shared_prefix=shared_prefix)
    return ranked[0] if ranked else None


def is_close_call(ranked: tuple[ItemPathCandidate, ...], *, ratio: float = 0.7) -> bool:
    """最高分与次高分是否**接近**（接近就该把候选报给用户，让他自己选）。

    只有一个候选、或最高分为 0 时不算接近呼叫 —— 那属于"没得选"，不是"选错了"。
    """
    if len(ranked) < 2 or ranked[0].score <= 0:
        return False
    return ranked[1].score >= ranked[0].score * ratio

"""内嵌 JSON 数据载荷识别（SPA 的 SSR 数据）。

## 为什么需要

现代前端（Next.js / Nuxt / Gatsby / 各种 window.__INITIAL_STATE__）会把**整页数据**
以 JSON 形式内嵌在一个 script 标签里，DOM 里只有一个空的挂载点：

    <div id="__next"></div>
    <script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"products":[...]}}}</script>

2026-11-27 实测：对这类页面走原来的 HTML 结构分析，结果是
page_type=single, patterns=0, fields=[] => AutoConfigUnverifiedError，
而**数据其实就在同一份 HTML 里**，一个字节都没少。用户要么去开浏览器渲染（慢一个数量级），
要么去手写选择器（页面上根本没有对应元素）。

所以判据不是"这个站点要不要渲染"，而是"**这份 HTML 里有没有可用的结构化数据**"。
有就不用渲染 —— 静态抓取即可拿到全部记录。

## 怎么认

两类信号，都只看**原始 HTML 文本**（不依赖 DOM 结构，因为数据本身就在 script 文本里）：

1. script type="application/json"（含 id="__NEXT_DATA__" / id="__NUXT_DATA__"
   这类 SSR 数据块）—— 现代框架的标准形态；
2. window.__XXX__ = {...} 形式的内联赋值（Nuxt / Gatsby / 各种自研站点）。

★ 刻意**不**认 application/ld+json：那是 jsonld 来源的职责，两者语义不同
（前者是"整页给搜索引擎的一份描述"，后者是"页面的业务数据"），混在一起会把
JSON-LD 的 ItemList 当成列表数据重复提取。

## 判定边界

- 只接受**能 json.loads 的**内容；window.__NUXT__={...()} 这种含函数调用的直接放弃
  （宁可不提取，也不猜）；
- 单个脚本超过 MAX_PAYLOAD_BYTES 直接跳过 —— 内嵌几十 MB 的 base64 图片不是数据；
- 记录判定复用 item_path.rank_item_paths，与 api_discovery / intelligent_scraper
  **同一份判据**，避免三处口径漂移。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from omnicrawler.extraction.item_path import (
    ItemPathCandidate,
    is_close_call,
    rank_item_paths,
)

#: 单个内嵌载荷的体积上限（字符数）。超过就当它不是业务数据。
#: 实测 Next.js 商品列表页的 __NEXT_DATA__ 在 20-400 KB 之间，8 MB 足够宽松。
MAX_PAYLOAD_BYTES = 8_000_000

#: 一页最多看几个候选脚本 —— 广告 / 埋点脚本里也常有 JSON，看太多只会更慢。
MAX_SCRIPTS = 24

_SCRIPT_RE = re.compile(
    r"<script\b([^>]*)>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)

_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))""")

#: window.__INITIAL_STATE__ = {...} 形式的内联赋值。
_ASSIGN_RE = re.compile(
    r"""(?:window\.)?(__[A-Z][A-Z0-9_]*__)\s*=\s*""",
)


@dataclass(frozen=True, slots=True)
class EmbeddedPayload:
    """一份内嵌载荷及其来源标识。"""

    source: str             # 人类可读的来源，如 script#__NEXT_DATA__
    payload: Any


def _attrs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw or ""):
        name = match.group(1).lower()
        value = match.group(2)
        if value is None:
            value = match.group(3)
        if value is None:
            value = match.group(4)
        out[name] = value or ""
    return out


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return stripped[0] in "[{"


def _loads(text: str) -> Any | None:
    """安全解析 JSON；超限/不合法/递归过深一律返回 None（不猜）。"""
    if len(text) > MAX_PAYLOAD_BYTES:
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _assignment_payload(text: str) -> Any | None:
    """从 window.__X__ = {...}; 这类内联赋值里取出 JSON 值。

    ★ 只在**整段赋值能被 JSON 解析**时才接受：Nuxt 3 的 window.__NUXT__ 可能含
      函数调用（...()），那种形态一律放弃 —— 半解析出来的半个对象比没有更糟。
    """
    match = _ASSIGN_RE.match(text.lstrip())
    if not match:
        return None
    rest = text.lstrip()[match.end():].strip()
    if not _looks_like_json(rest):
        return None
    rest = rest.rstrip()
    while rest.endswith(";"):
        rest = rest[:-1].rstrip()
    return _loads(rest)


def extract_embedded_payloads(html: str) -> list[EmbeddedPayload]:
    """按出现顺序返回页面里所有**可解析的**内嵌 JSON 载荷。

    ★ 2026-11-27：配额从"只看前 N 个 `<script>` 标签"改成"最多**解析** N 次"。

      实测 walmart.com：`__NEXT_DATA__` 是第 **126** 个 `<script>` 标签
      （前面压着 125 个埋点/预加载脚本），旧实现只看前 24 个 ⇒ 明明有 600 KB 内嵌
      数据却报"没找到"，于是这个能力在最需要它的站点上**静默失效**。

      属性判断（type/id）是廉价的字符串操作，扫多少个标签都不贵；贵的是 `json.loads`。
      所以配额卡在解析次数上 —— 既覆盖了"数据块被压到很后面"，又仍然挡住
      "一页几百个 JSON 脚本"的极端情形。
    """
    if not html:
        return []
    found: list[EmbeddedPayload] = []
    parsed = 0

    for match in _SCRIPT_RE.finditer(html):
        attrs = _attrs(match.group(1))
        script_type = (attrs.get("type") or "").strip().casefold()
        script_id = (attrs.get("id") or "").strip()
        # ★ ld+json 归 jsonld 来源，不重复计入内嵌数据（见模块 docstring）
        if script_type == "application/ld+json":
            continue
        is_json_type = script_type in {"application/json", "text/json"}
        if not is_json_type:
            # 内联脚本：只有"像 SSR 状态"的才继续看，避免逐个解析埋点脚本
            body_head = (match.group(2) or "").lstrip()
            if not body_head.startswith(("window.", "self.", "var ", "const ", "let ")):
                continue
            if not _ASSIGN_RE.match(body_head) and "__NEXT_DATA__" not in body_head:
                continue
        if parsed >= MAX_SCRIPTS:
            break
        parsed += 1
        body = match.group(2)
        if not body or len(body) > MAX_PAYLOAD_BYTES:
            continue
        label = f"script#{script_id}" if script_id else "script"
        if is_json_type:
            value = _loads(body)
            if value is None:
                continue
            found.append(EmbeddedPayload(label, value))
            continue
        stripped = body.lstrip()
        value = _assignment_payload(stripped)
        if value is None:
            continue
        name_match = _ASSIGN_RE.match(stripped)
        label = f"script:{name_match.group(1)}" if name_match else label
        found.append(EmbeddedPayload(label, value))
    return found

#: schema.org 里"一串条目"的类型名（大小写不敏感）。
#: ★ 只认这两个：它们是**列表**语义；其它 @type（Product / Article / BreadcrumbList）
#:   要么是单对象、要么是导航，当成记录集合会给出一条假列表。
_ITEM_LIST_TYPES = frozenset({"itemlist", "listitem"})

#: JSON-LD 里条目数组的键名（schema.org 的官方拼写 + 常见变体）。
_ITEM_LIST_KEYS = ("itemListElement", "itemListElements", "items", "elements")


def _ld_documents(html: str) -> list[Any]:
    """把页面里所有 `application/ld+json` 解析成 Python 对象（失败即跳过）。"""
    found: list[Any] = []
    if not html or "ld+json" not in html:
        return found
    for match in _SCRIPT_RE.finditer(html):
        attrs = _attrs(match.group(1))
        if (attrs.get("type") or "").strip().casefold() != "application/ld+json":
            continue
        value = _loads(match.group(2))
        if value is None:
            continue
        if isinstance(value, list):
            found.extend(value)
        else:
            found.append(value)
    return found


def _item_list_entries(document: Any) -> list[dict[str, Any]] | None:
    """一个 JSON-LD 文档里如果含 ItemList，返回它的条目（只留 dict 条目）。

    `@graph` 是 schema.org 的容器写法（一个 script 里放多个对象），要下探一层。
    """
    if not isinstance(document, dict):
        return None
    candidates = [document]
    graph = document.get("@graph")
    if isinstance(graph, list):
        candidates.extend(item for item in graph if isinstance(item, dict))
    for candidate in candidates:
        type_name = str(candidate.get("@type", "")).casefold()
        if type_name not in _ITEM_LIST_TYPES:
            continue
        for key in _ITEM_LIST_KEYS:
            entries = candidate.get(key)
            if isinstance(entries, list):
                dicts = [item for item in entries if isinstance(item, dict)]
                if len(dicts) >= 2:
                    return dicts
    return None


def extract_itemlist_payloads(html: str) -> list[EmbeddedPayload]:
    """把页面 JSON-LD 里的 `ItemList` 抽成**记录集合**载荷。

## 为什么这是"内嵌数据"而不是 jsonld 来源

`jsonld` 来源的语义是「整页那**一份**描述」——它取一次，单独成一条 document 记录。
而 `ItemList` 是**列表**：每一条 entry 是一条记录。当成文档级字段取，用户只能拿到
一个装了几十个 dict 的单元格，或者干脆什么都不生成。

实测（2026-11-27）：一个只在 JSON-LD 里给出 `ItemList` 的页面，走结构分析得到
`page_type=single`，产出一份只有一个 `标题_1`（取值是页面大标题）的配置 ——
**看起来成功，实际一条业务记录都没有**。

★ 与 `jsonld` 来源**不重复**：这里只产出 ItemList 的条目数组，不搬运其余 LD 字段。
"""
    found: list[EmbeddedPayload] = []
    for document in _ld_documents(html):
        entries = _item_list_entries(document)
        if not entries:
            continue
        found.append(EmbeddedPayload("jsonld#ItemList", {"itemListElement": entries}))
    return found

@dataclass(frozen=True, slots=True)
class EmbeddedRecords:
    """页面内嵌数据里最像"记录集合"的那一处。"""

    source: str
    payload: Any
    candidate: ItemPathCandidate

    @property
    def item_path(self) -> str:
        return self.candidate.path

    @property
    def sample(self) -> Any:
        return self.candidate.sample

    @property
    def count(self) -> int:
        return self.candidate.item_count


#: 内嵌载荷的嵌套遍历配额（所有脚本合计）。
#: 实测 Next.js 商品页的 __NEXT_DATA__ 有数百个嵌套对象，全扫一遍纯属浪费 ——
#: 记录集合几乎总在前两层（与 item_path 的 _MAX_ARRAY_DEPTH 一致）。
_MAX_TRAVERSE = 400

#: 一个数组要算"记录集合"，元素里至少得有这么多**业务字段**吧？
#: Nuxt 的公共前缀数组 [{"id":1},{"id":2}] 只有 1 个字段 —— 那种残片不该被当成
#: 完整记录交付（用户以为拿到了列表，实际每行只有一个 ID）。
_MIN_RECORD_FIELDS = 2

#: 内嵌数据的记录路径**搜索深度**（0 = `$.a[*]`，1 = `$.a.b[*]`，2 = `$.a.b.c[*]`）。
#: 实测 Next.js 把列表放在 `props.pageProps.products` —— 那是深度 2。
#: 深度每加一层，候选数按嵌套对象数指数增长，所以只放宽到这一层就够。
_MAX_EMBEDDED_DEPTH = 3


def _scalar_field_count(sample: Any) -> int:
    return sum(1 for value in sample.values() if not isinstance(value, (dict, list)))


def find_embedded_records(html: str, url: str = "") -> EmbeddedRecords | None:
    """在页面内嵌 JSON 里找**最像记录集合**的那一处；找不到返回 None。

    判据完全复用 item_path.rank_item_paths（与 API 发现、JSON 自动配置同一份），
    因此"从内嵌数据里认出的列表"与"从 API 响应里认出的列表"口径一致。

    ★ 三个额外约束，都是为了"**宁可不提取，也不交残片**"：

    1. shared_prefix=True —— 认出 Nuxt 2 的"公共前缀 + 剩余行"形态并合并；
    2. 元素至少 _MIN_RECORD_FIELDS 个业务字段 —— 只有 ID 的残片不算记录；
    3. 遍历有 _MAX_TRAVERSE 配额 —— 载荷再大也不会把分析拖成几十秒。
    """
    best: EmbeddedRecords | None = None
    # ★ 2026-11-27：JSON-LD 的 `ItemList` 也是"内嵌数据"，而且常常是**唯一**的结构化列表
    #   （实测：只有 LD ItemList 的页面产出"一个 标题_1 列"，看起来成功、实际没数据）。
    #   它与 `jsonld` 来源不重复 —— 这里只取条目数组，不搬运其余 LD 字段。
    payloads = extract_embedded_payloads(html) + extract_itemlist_payloads(html)
    for item in payloads:
        ranked = rank_item_paths(
            item.payload,
            url=url,
            shared_prefix=True,
            budget=_MAX_TRAVERSE,
            max_depth=_MAX_EMBEDDED_DEPTH,
        )
        for candidate in ranked:
            sample = candidate.sample
            if not isinstance(sample, dict) or candidate.score <= 0:
                continue
            if candidate.path != "$" and _scalar_field_count(sample) < _MIN_RECORD_FIELDS:
                continue
            if best is None or candidate.score > best.candidate.score:
                best = EmbeddedRecords(item.source, item.payload, candidate)
            break
    return best


def describe_choice(records: EmbeddedRecords, url: str = "") -> list[str]:
    """把"从哪块数据、按什么路径取的"说清楚 —— 供 advisories 使用。

    ★ 只说**非显而易见**的情况（与 _item_path_advice 同一条纪律）：正常命中时不发言。
    """
    ranked = rank_item_paths(
        records.payload,
        url=url,
        shared_prefix=True,
        budget=_MAX_TRAVERSE,
        max_depth=_MAX_EMBEDDED_DEPTH,
    )
    head = (
        "页面没有可用的列表 DOM，但内嵌数据里有结构化记录："
        f"{records.source} 的 {records.item_path}（{records.count} 条），已按静态抓取处理"
    )
    if records.item_path == "$" or not is_close_call(ranked):
        return [head]
    listing = "；".join(f"{c.path}（{c.score} 分）" for c in ranked[:3])
    return [
        head,
        f"内嵌数据里有多个接近的记录路径，已取最高分 {records.item_path}；候选：{listing}",
    ]

"""智能爬虫 — 自动分析网页 DOM 结构，推断列表/详情/分页模式，生成字段配置。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omnicrawler.core.utils import user_agent
from omnicrawler.extraction.item_path import (
    ItemPathCandidate as ItemPathCandidate,
)
from omnicrawler.extraction.item_path import (
    is_close_call as is_close_call,
)
from omnicrawler.extraction.item_path import (
    is_representable as is_representable,
)
from omnicrawler.extraction.item_path import (
    rank_item_paths as rank_item_paths,
)
from omnicrawler.extraction.item_path import (
    unrepresentable_path_keys as unrepresentable_path_keys,
)

from .dom_features import (
    _CSS_IDENT as _CSS_IDENT,
)
from .dom_features import (
    DOMNode as DOMNode,
)
from .dom_features import (
    _build_css as _build_css,
)
from .dom_features import (
    _parse_dom as _parse_dom,
)
from .dom_features import (
    _parse_dom_lxml as _parse_dom_lxml,
)
from .dom_features import (
    _parse_dom_selectolax as _parse_dom_selectolax,
)
from .dom_features import (
    _tag_name as _tag_name,
)
from .dom_features import (
    _walk as _walk,
)
from .field_inference import (
    _FIELD_CANDIDATES as _FIELD_CANDIDATES,
)
from .field_inference import (
    _FIELD_RULES as _FIELD_RULES,
)
from .field_inference import (
    _INTERACTION_SIGNALS as _INTERACTION_SIGNALS,
)
from .field_inference import (
    _ITEMPROP_NAMES as _ITEMPROP_NAMES,
)
from .field_inference import (
    _MAX_SINGLE_PAGE_FIELDS as _MAX_SINGLE_PAGE_FIELDS,
)
from .field_inference import (
    _SCRIPT_RE as _SCRIPT_RE,
)
from .field_inference import (
    _SCROLL_MARKUP_RE as _SCROLL_MARKUP_RE,
)
from .field_inference import (
    _SCROLL_ROUNDS as _SCROLL_ROUNDS,
)
from .field_inference import (
    _SCROLL_WIRING_RE as _SCROLL_WIRING_RE,
)
from .field_inference import (
    _WORD_NUMBERS as _WORD_NUMBERS,
)
from .field_inference import (
    _ancestor_class_context as _ancestor_class_context,
)
from .field_inference import (
    _class_value_node as _class_value_node,
)
from .field_inference import (
    _class_value_token as _class_value_token,
)
from .field_inference import (
    _classify_field as _classify_field,
)
from .field_inference import (
    _css_token as _css_token,
)
from .field_inference import (
    _explain_no_config as _explain_no_config,
)
from .field_inference import (
    _field_key as _field_key,
)
from .field_inference import (
    _fields_for_pattern as _fields_for_pattern,
)
from .field_inference import (
    _has_semantic_signal as _has_semantic_signal,
)
from .field_inference import (
    _infer_item_fields as _infer_item_fields,
)
from .field_inference import (
    _infer_leaf_item_fields as _infer_leaf_item_fields,
)
from .field_inference import (
    _infer_single_page_fields as _infer_single_page_fields,
)
from .field_inference import (
    _interaction_advice as _interaction_advice,
)
from .field_inference import (
    _interaction_failure_hint as _interaction_failure_hint,
)
from .field_inference import (
    _interaction_signals as _interaction_signals,
)
from .field_inference import (
    _is_value_class_part as _is_value_class_part,
)
from .field_inference import (
    _itemprop_field_name as _itemprop_field_name,
)
from .field_inference import (
    _observed_value_map as _observed_value_map,
)
from .field_inference import (
    _parent_css as _parent_css,
)
from .field_inference import (
    _product_detail_fields as _product_detail_fields,
)
from .field_inference import (
    _rel_selector as _rel_selector,
)
from .field_inference import (
    _relative_segments as _relative_segments,
)
from .field_inference import (
    _strip_value_classes as _strip_value_classes,
)
from .field_inference import (
    _unique_field_name as _unique_field_name,
)
from .field_inference import (
    _unique_relative_selector as _unique_relative_selector,
)
from .field_inference import (
    _usable_field_count as _usable_field_count,
)
from .field_inference import (
    infer_fields as infer_fields,
)
from .field_inference import (
    suggest_closest_field as suggest_closest_field,
)
from .pagination import (
    _PAGINATION_PARAMS as _PAGINATION_PARAMS,
)
from .pagination import (
    _iframe_locators as _iframe_locators,
)
from .pagination import (
    _max_page_number as _max_page_number,
)
from .pagination import (
    _page_parameter_in as _page_parameter_in,
)
from .pagination import (
    _pagination_config as _pagination_config,
)
from .pagination import (
    detect_pagination as detect_pagination,
)
from .pattern_detector import (
    _CHROME_TOKENS as _CHROME_TOKENS,
)
from .pattern_detector import (
    _HEADING_TAGS as _HEADING_TAGS,
)
from .pattern_detector import (
    _MIN_LIST_ITEMS as _MIN_LIST_ITEMS,
)
from .pattern_detector import (
    _VALUE_CHAR_RE as _VALUE_CHAR_RE,
)
from .pattern_detector import (
    RepeatingPattern as RepeatingPattern,
)
from .pattern_detector import (
    _distinct_child_tags as _distinct_child_tags,
)
from .pattern_detector import (
    _has_value_char as _has_value_char,
)
from .pattern_detector import (
    _is_assembled_container as _is_assembled_container,
)
from .pattern_detector import (
    _is_chrome_path as _is_chrome_path,
)
from .pattern_detector import (
    _is_heading_key as _is_heading_key,
)
from .pattern_detector import (
    _items_by_signature as _items_by_signature,
)
from .pattern_detector import (
    _node_signature as _node_signature,
)
from .pattern_detector import (
    _path_contains as _path_contains,
)
from .pattern_detector import (
    _squash_ws as _squash_ws,
)
from .pattern_detector import (
    _suppress_nested as _suppress_nested,
)
from .pattern_detector import (
    detect_repeating_patterns as detect_repeating_patterns,
)
from .result_quality import (
    meaningful_field_names as _meaningful_field_names,
)
from .result_quality import (
    quality_rank as _result_quality_rank,
)
from .result_quality import (
    verdict as _result_verdict,
)

# ── 主入口 ─────────────────────────────────────────────────────────────

@dataclass
class IntelligentAnalysis:
    """智能分析结果。"""
    url: str
    patterns: list[RepeatingPattern] = field(default_factory=list)
    fields: list[dict[str, Any]] = field(default_factory=list)
    pagination: dict[str, Any] | None = None
    page_type: str = "unknown"      # list / detail / single / gallery / search
    confidence: float = 0.0


def analyze_page(html: str, url: str = "") -> IntelligentAnalysis:
    """分析页面结构，一次调用完成全部推断。

    Args:
        html: 页面 HTML 内容。
        url: 页面 URL（用于分页检测）。

    Returns:
        IntelligentAnalysis 包含模式、字段、分页信息。
    """
    nodes = _parse_dom(html)
    patterns = detect_repeating_patterns(nodes)
    fields = infer_fields(patterns, nodes, url)
    pagination = detect_pagination(html, url)

    # 判定页面类型
    if any(field.get("record_grain") == "detail" for field in fields):
        page_type = "detail"
        confidence = 0.9
    elif patterns and patterns[0].count >= 3:
        page_type = "list"
        confidence = min(0.95, 0.5 + patterns[0].score * 0.5)
    elif fields and len(fields) > 5:
        page_type = "detail"
        confidence = 0.7
    elif len(nodes) < 50:
        page_type = "single"
        confidence = 0.5
    else:
        page_type = "unknown"
        confidence = 0.3

    return IntelligentAnalysis(
        url=url, patterns=patterns, fields=fields,
        pagination=pagination, page_type=page_type, confidence=confidence,
    )


# ── JSON / API 载荷识别 ────────────────────────────────────────────────

_JSON_FIELD_NAMES: dict[str, str] = {
    "name": "名称", "title": "标题", "headline": "标题",
    "price": "价格", "amount": "金额", "cost": "金额",
    "author": "作者", "brand": "品牌", "category": "分类",
    "description": "描述", "desc": "描述", "body": "正文", "content": "正文",
    "email": "邮箱", "phone": "电话", "telephone": "电话", "mobile": "手机",
    "id": "编号", "sku": "编号", "code": "编号",
    "stock": "库存", "quantity": "库存", "inventory": "库存", "availability": "库存状态",
    "url": "链接地址", "link": "链接地址", "image": "图片地址", "thumbnail": "图片地址",
    "date": "日期", "created_at": "创建日期", "updated_at": "更新日期", "published_at": "发布日期",
    "rating": "评分", "score": "评分", "tags": "标签", "city": "城市", "country": "国家",
    "address": "地址", "latitude": "纬度", "longitude": "经度",
}


def _maybe_json_payload(text: str) -> Any | None:
    """若整段响应体是可解析的 JSON，返回解析结果，否则 None。"""
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "[{":
        return None
    if len(stripped) > 8_000_000:
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


#: 单条 JSON 记录铺开后最多几列。★ 旧实现是 `list(sample.items())[:12]` ——
#: 按**字典插入序**砍掉第 13 个键之后的字段，静默缺列（走查 R4.2 一并修掉）。
#: 上限本身是为了挡住"记录有 60 个键 ⇒ 60 列宽表"，被省略的列必须**报出来**。
_JSON_FIELDS_CAP = 32

#: 嵌套**对象**最多铺平到第几层：`company.name`、`company.address.city`。
_JSON_NESTING_CAP = 2


def _json_fields(sample: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """把一条样本记录铺成字段表；返回 ``(fields, notes)``。

    走查 R4.2：旧实现 `if isinstance(value, (dict, list)): continue` 把 ``company`` /
    ``address`` 这类**嵌套对象整列丢掉**，用户拿到的是一张缺列的表。现在：

    - **嵌套对象**铺平成 ``parent.child``（至多 ``_JSON_NESTING_CAP`` 层）；
    - **标量数组**保留整列（值就是这个列表）；
    - **对象数组**不铺成列（会撑爆列数）—— 但**列进提醒**，不静默丢。
    """
    fields: dict[str, Any] = {}
    notes: list[str] = []
    nested_arrays: list[str] = []
    too_deep: list[str] = []
    omitted: list[str] = []

    def label_for(path: str) -> str:
        """优先用叶子键的中文标签；重名则退回完整路径（保证列名唯一）。"""
        leaf = path.rsplit(".", 1)[-1]
        label = _JSON_FIELD_NAMES.get(leaf.casefold(), leaf)
        if label not in fields:
            return label
        candidate, index = path, 2
        while candidate in fields:
            candidate = f"{path}_{index}"
            index += 1
        return candidate

    def add_column(path: str, value: Any) -> None:
        if len(fields) >= _JSON_FIELDS_CAP:
            omitted.append(path)
            return
        fields[label_for(path)] = {"path": path}

    def visit(path: str, value: Any, depth: int) -> None:
        if isinstance(value, dict):
            if depth >= _JSON_NESTING_CAP:
                too_deep.append(path)
                return
            for key, child in value.items():
                segment = str(key)
                if not is_representable(segment):
                    continue  # 无法表达成 JSONPath ⇒ 由 _json_configs 统一提醒
                visit(f"{path}.{segment}", child, depth + 1)
            return
        if isinstance(value, list):
            if value and all(isinstance(item, dict) for item in value[:5]):
                nested_arrays.append(path)
                return
            add_column(path, value)
            return
        add_column(path, value)

    for key, value in sample.items():
        segment = str(key)
        if not is_representable(segment):
            continue
        visit(segment, value, 0)

    if nested_arrays:
        notes.append(
            "以下嵌套数组未铺成列（记录内的对象数组会撑爆列数）："
            + "、".join(nested_arrays[:5])
            + "；如需取其中某个子字段，可手工加一个 extract.fields 项、path 写 "
            + f"{nested_arrays[0]}[*].<子字段>（多个匹配取首个，加 all: true 取全部）"
        )
    if too_deep:
        notes.append(
            f"以下嵌套超过 {_JSON_NESTING_CAP} 层未展开：" + "、".join(too_deep[:5])
        )
    if omitted:
        notes.append(
            f"字段数超过 {_JSON_FIELDS_CAP}，以下列已省略：" + "、".join(omitted[:5])
        )
    return fields, notes


def _item_path_advice(
    ranked: tuple[ItemPathCandidate, ...], chosen: ItemPathCandidate
) -> list[str]:
    """记录路径"拿不太准"时把候选说出来（走查 R4.2）。

    ★ 只在**非显而易见**时发言：选了单对象（``$``）、或最高分与次高分接近
    （`is_close_call`）。否则每条 JSON 分析都刷一行提醒，提醒就会被当噪声忽略
    —— 这是 R4.1 已经定过的调子。
    """
    if chosen.path != "$" and not is_close_call(ranked):
        return []
    listing = "；".join(
        f"{item.path}（{item.score} 分：{'、'.join(item.reasons)}）" for item in ranked[:3]
    )
    head = (
        "该 JSON 响应按「单对象」处理（item_path = $，整份响应即一条记录）"
        if chosen.path == "$"
        else f"记录路径有多个接近的候选，已取最高分 {chosen.path}"
    )
    return [f"{head}；可用 --item-path 改选。候选：{listing}"]


def _json_configs(
    payload: Any,
    url: str,
    project_name: str,
    *,
    advisories: list[str] | None = None,
    item_path_override: str = "",
) -> dict[str, Any] | None:
    """把 JSON API 响应转成 ``source.kind: rest`` + ``extract.mode: json`` 配置。

    实测背景（2026-09-12 场景测试）：对 ``jsonplaceholder.typicode.com/users``
    这类纯 API 地址，旧实现只走 HTML 分析路径——响应体是 JSON、DOM 里没有列表，
    于是生成 ``fields: {}`` 的空配置并"成功"退出，用户拿到 0 条记录。
    而 JSON 抽取链路本身完备（同一地址手工配 ``mode: json`` 可稳定取到
    10/10 条、字段完整度 1.0）——缺的只是**路由**。

    走查 R4.2 起，记录路径改**候选打分**（判据在 ``extraction/item_path.py``，
    与 ``api_discovery`` 共用一处），不再"第一个对象数组"；``item_path_override``
    是用户显式指定的路径（``auto-analyze --item-path``），优先于打分。
    """
    from omnicrawler.extraction.extractors import json_path

    ranked = rank_item_paths(payload, url=url)
    chosen: ItemPathCandidate | None = None

    if item_path_override:
        chosen_path = item_path_override
        values = json_path(payload, chosen_path)
        if not values:
            raise AutoConfigUnverifiedError(
                f"--item-path 在这份响应里取不到任何记录：{chosen_path}"
                "\n  可供选择的候选：" + ("；".join(item.path for item in ranked[:3]) or "（无）")
            )
        sample = values[0]
    else:
        if not ranked:
            return None
        chosen = ranked[0]
        chosen_path, sample = chosen.path, chosen.sample

    fields, notes = _json_fields(sample) if isinstance(sample, dict) else ({}, [])
    if not fields and isinstance(sample, dict):
        return None

    if advisories is not None:
        if chosen is not None:
            advisories.extend(_item_path_advice(ranked, chosen))
        unrepresentable = unrepresentable_path_keys(sample)
        if unrepresentable:
            advisories.append(
                "样本里这些键名含 `.` 或 `[]`，现有 JSONPath 无法表达，已跳过："
                + "、".join(unrepresentable[:5])
            )
        advisories.extend(notes)

    return {
        "project": {"name": project_name},
        "source": {"kind": "rest", "seeds": [url]},
        "crawl": {"max_pages": 1, "same_host": True},
        "http": {"user_agent": user_agent("+bot"), "respect_robots": True},
        "extract": {"mode": "json", "item_path": chosen_path, "fields": fields},
        "outputs": {"jsonl": True, "csv": True, "xlsx": True},
    }


# ── 自我验证：把"猜出来的配置"拿真实抽取器跑一遍 ────────────────────────

class AutoConfigUnverifiedError(RuntimeError):
    """自动生成的配置在真实页面上采不到任何记录。"""


def verify_config(config: dict[str, Any], html: str) -> dict[str, Any]:
    """在给定 HTML 上试跑生成的配置，返回每个字段的实际填充情况。

    这一步是"自动配置"的验收门禁：不验证的推断等于没推断——旧实现会在
    产出 ``0 条记录`` 的配置后照样退出码 0、状态 succeeded，用户拿到空结果
    却看不到任何错误（2026-09-12 场景测试实测）。验证复用**抽取器同一套
    选择器引擎**（``html_tools``），所以结论与真实 run 一致。
    """
    extract = config.get("extract", {})
    if str(extract.get("mode", "")) == "json":
        payload = _maybe_json_payload(html)
        fields = extract.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        json_filled: dict[str, int] = {str(name): 0 for name in fields}
        if payload is None:
            return {"mode": "json", "items": 0, "records": 0, "fields": json_filled}

        # 与 JSONProcessor 使用同一个 JSONPath 求值器，并按相同的 paths/path
        # 回退顺序试跑字段。旧实现只要响应能 json.loads 就把 item、record 和
        # 每个字段都伪报为 1，错误 item_path / 字段路径也会通过自动配置门禁。
        from omnicrawler.extraction.extractors import json_field_values, json_path

        items = json_path(payload, str(extract.get("item_path", "$")))
        records = 0
        for item in items:
            if not fields:
                records += 1
                continue
            has_value = False
            for name, rule in fields.items():
                _path, values = json_field_values(item, name, rule)
                if values:
                    json_filled[str(name)] += 1
                    has_value = True
            if has_value:
                records += 1
        return {"mode": "json", "items": len(items), "records": records, "fields": json_filled}

    from omnicrawler.extraction.html_tools import node_attr, node_text, parse_html, select_nodes

    document = parse_html(html)
    item_selector = str(extract.get("item_selector", "") or "")
    items = select_nodes(document, item_selector) if item_selector else [document]
    fields = extract.get("fields", {})
    filled: dict[str, int] = {str(name): 0 for name in fields}
    records = 0
    for item in items:
        data: dict[str, Any] = {}
        for name, rule in fields.items():
            if not isinstance(rule, dict):
                continue
            selector = str(rule.get("selector", "") or "")
            nodes = select_nodes(item, selector) if selector else [item]
            if not nodes:
                continue
            node = nodes[0]
            # 2026-11-26: 与 extractors._apply_rule 保持**同一口径** ——
            #   count 型字段的值是子元素个数（星级 = N 个 svg）。
            #   这里漏了这条分支，验证报告会显示 评分(0)，而真实 run 能取到；
            #   反过来也会让"验证通过、运行 0 条"的假绿重新出现。
            child_selector = str(rule.get("count_selector") or "")
            value: Any
            if child_selector:
                value = len(select_nodes(node, child_selector))
            else:
                value = node_attr(node, str(rule["attr"])) if rule.get("attr") else node_text(node)
            if value not in (None, ""):
                data[str(name)] = value
                filled[str(name)] += 1
        if data:
            records += 1
    return {"mode": "html", "items": len(items), "records": records, "fields": filled}


def _check_verified(config: dict[str, Any], html: str) -> dict[str, Any]:
    """试跑生成的配置；采不到足够记录即抛错（不再静默产出不可用配置）。

    三道门槛（前两条 2026-09-13 新增，起因是实测 scrapeme.live 首页"商品其实在 ``/shop/``"）：

    1. **容器落在页面框架（导航 / 侧边栏 / 页脚）⇒ 判为"没找到列表"**。旧实现会产出
       一份指向 ``aside.sidebar > a`` 的配置并"试跑通过"（4 条记录）——用户拿到的是侧边栏。
    2. **列表项过少 ⇒ 同样判为"没找到列表"**。已写入 ``item_selector`` 却只匹配到 1–2 个
       元素，那不是"一个很小的列表"，而是容器选错了；旧逻辑在 ``items <= 2`` 时把门槛
       降到 1，于是 1 条也放行。
       ⚠️ **单页 / 单对象模式不适用前两条**：没有 ``item_selector`` 时 ``items`` 恒为 1
       （整页即"一个对象"），那是另一种模式，不是选错容器。
    3. 门槛不是"至少 1 条"——列表页只采到 1 条通常说明容器/字段选错了（例如 10 条里
       只有 1 条命中），所以有多个列表项时要求至少 2 条。
    """
    report = verify_config(config, html)
    items = int(report.get("items") or 0)
    extract = config.get("extract", {})
    empty = ", ".join(f"{k}({v})" for k, v in report["fields"].items()) or "（无字段）"

    item_selector = str(extract.get("item_selector", "") or "")
    detail = _product_detail_fields(_parse_dom(html)) if extract.get("record_grain") == "detail" else []
    verified_detail = bool(detail) and items == 1 and detail[0]["selector"] == item_selector
    is_html_list = str(extract.get("mode", "")) != "json" and bool(item_selector) and not verified_detail

    # 3. 容器落在**页面框架**（导航 / 侧边栏 / 页脚）里 ⇒ 那不是业务列表。
    #    实测（本轮复现）：只含侧边栏链接的页面会产出一份指向 `aside.sidebar > a` 的配置并
    #    "试跑通过"（4 条记录）—— 用户拿到的是侧边栏，不是商品列表。与其如此，不如明确报错。
    #    判据复用打分层已有的 `_is_chrome_path`（_CHROME_TOKENS = aside / nav / footer），
    #    作用面很窄：真正的业务列表极少落在这些容器里；真落在里面时下面的建议给出出路。
    if is_html_list and _is_chrome_path(item_selector):
        raise AutoConfigUnverifiedError(
            "未识别出列表：只在页面框架（导航 / 侧边栏 / 页脚）里找到重复元素。"
            f"\n  item_selector = {item_selector}"
            f"\n  字段填充情况 = {empty}"
            "\n  建议：先确认目标 URL 就是列表页（商品列表常在 /shop、/products 等路径下，"
            "首页往往只有导航与侧边栏）；若该页面确实有列表但结构不常规，"
            "请用 `omnicrawler visual-select` 手动圈选字段。"
        )

    if is_html_list and items < _MIN_LIST_ITEMS:
        raise AutoConfigUnverifiedError(
            f"未识别出列表：`item_selector` 只匹配到 {items} 个元素"
            f"（少于 {_MIN_LIST_ITEMS} 个），不构成列表。"
            f"\n  item_selector = {item_selector}"
            f"\n  字段填充情况 = {empty}"
            "\n  建议：先确认目标 URL 就是列表页（商品列表常在 /shop、/products 等路径下，"
            "首页往往只有导航与侧边栏）；若确有列表但结构不常规，可用 "
            "`omnicrawler visual-select` 手动圈选字段，或 `omnicrawler templates inspect <URL>` 查看识别结果。"
        )

    floor = 1 if items <= 2 else 2
    if report["records"] >= floor:
        return report

    # 说清"到底识别到了什么"：没有 item_selector 时 items 恒为 1（整页即一个对象），
    # 说成"识别出 1 个列表项"会误导排查方向。
    scope = (
        f"识别出 {items} 个列表项，至少需要 {floor} 条"
        if item_selector
        else "未识别出列表容器，已按整页单对象模式试跑"
    )
    raise AutoConfigUnverifiedError(
        f"自动分析未能产出可用配置：在目标页面上试跑只得到 {report['records']} 条记录（{scope}）。"
        f"\n  item_selector = {extract.get('item_selector', '') or '（未识别出列表容器）'}"
        f"\n  字段填充情况 = {empty}"
        "\n  建议：该页面可能是 JS 动态渲染、需要登录，或列表结构不常规——"
        "可改用 `omnicrawler templates inspect <URL>` 查看站点识别结果，"
        "或 `omnicrawler visual-select` 手动圈选字段。"
    )


def analyze_to_config(
    html: str,
    url: str = "",
    project_name: str = "auto_task",
    *,
    rendered: bool = False,
    force_browser: bool = False,
    scroll_rounds: int = 0,
    item_path_override: str = "",
    advisories: list[str] | None = None,
) -> dict[str, Any]:
    """分析页面并直接生成符合 core/config.py 契约的 OmniCrawler 配置。

    Args:
        html: 用于分析的页面 HTML。
        url: 真实页面地址（占位地址不允许）。
        rendered: 这份 HTML 是否来自浏览器渲染。**决定生成的 `source.kind`。**
        force_browser: 显式强制浏览器抓取（判定失手时的逃生阀）。
        scroll_rounds: >0 时产出浏览器滚动动作（内容随滚动追加的页面，走查 R3.2）。
        item_path_override: 用户显式指定的 JSON 记录路径（``--item-path``），
            跳过自动打分；**只对 JSON 载荷生效**，未生效时如实写进 `advisories`。
        advisories: 传入列表则把面向用户的提醒（候选路径、未展开的嵌套等）追加进去。
            ★ 这些提醒是「拿不太准 / 有东西没展开」的如实告知，不是日志。

    Raises:
        ValueError: 未提供真实 URL（占位符不允许）或契约核验失败时抛出。
        AutoConfigUnverifiedError: 生成的配置在真实页面上采不到记录时抛出。
    """
    url = (url or "").strip()
    if not url or url.startswith("file://"):
        raise ValueError("自动配置需要真实页面 URL：占位地址（如 file:///placeholder）不能通过校验，请补充 URL")

    # 先判 JSON/API 载荷：纯 API 地址走 rest + json 链路，不进 HTML 分析
    payload = _maybe_json_payload(html)
    if payload is not None:
        json_config = _json_configs(
            payload,
            url,
            project_name,
            advisories=advisories,
            item_path_override=item_path_override,
        )
        if json_config is not None:
            _check_verified(json_config, html)
            return json_config
        if item_path_override and advisories is not None:
            advisories.append(
                f"--item-path={item_path_override} 已给出，但该响应生成不出 JSON 配置"
                "（样本里没有可用字段），已回退到 HTML 分析"
            )
    elif item_path_override and advisories is not None:
        advisories.append(
            f"--item-path={item_path_override} 只对 JSON 载荷生效，本页不是 JSON，该参数未生效"
        )

    analysis = analyze_page(html, url)

    fields_dict: dict[str, Any] = {}
    item_selector = ""
    for f in analysis.fields:
        if f.get("is_container"):
            item_selector = str(f["selector"])
            continue
        name = f["name"]
        rule: dict[str, Any] = {"selector": f["selector"]}
        attribute = f.get("attribute") or "text"
        if attribute not in ("", "text"):
            rule["attr"] = attribute
        if f.get("all"):
            rule["all"] = True
        if f.get("regex"):
            rule["regex"] = f["regex"]
        # 走查 R3.6：值写在 class 名里时，取到 `class` 还要映射（`Three` → 3）
        if f.get("value_map"):
            rule["value_map"] = f["value_map"]
        # 2026-11-26: 有的字段值是**子元素个数**（星级渲染成 N 个 svg）。
        #   只写 attr 取不到东西，必须把计数选择器一起带下去。
        if f.get("count_selector"):
            rule["count_selector"] = f["count_selector"]
        if f.get("examples"):
            rule["examples"] = f["examples"]
        fields_dict[name] = rule

    # 走查 R3.1：**只在真需要时才用浏览器**。
    #
    # 旧实现无条件写 `source.kind: browser` + playwright。实测（0.13.0）静态站点也因此
    # 全程走浏览器（60 页 98s）；另一例在浏览器路径下 600s 超时未完成。
    # 而同一页面上 `templates inspect` 早已判出 `dynamic: false`、`browser_recommended: false`、
    # `pagination: ["next-link"]` —— 判断是有的，只是没接进这条路径。
    #
    # 判据不是"页面看起来动态"，而是**这份配置是从哪份 HTML 得出的**：
    #   · 静态 HTML 就足以得到可用配置 ⇒ 运行期也用静态抓取（`rendered=False`）
    #   · 只有渲染后的 HTML 才得到配置   ⇒ 运行期必须用浏览器（`rendered=True`）
    # 这样"分析用什么、运行就用什么"，不会出现「分析靠渲染、运行靠静态」的错配。
    #
    # ★ 静态路径还要分两步走（第一版只写 `static_html`，实测**立刻回归**）：
    #   `static_html` **不参与链接发现**（`sources.py` 的 `can_crawl` 不含它），
    #   于是同一站点只抓到 1 页 20 条（浏览器路径是 60 页 577 条）。
    #   需要翻页/进详情 ⇒ 用 `crawl`（静态 HTTP 抓取 + 链接发现，`render` 仅 browser 为真）；
    #   确实是单页无链接可跟 ⇒ 才用 `static_html`。
    # `force_browser` 是显式逃生阀：判定失手时仍可强制走浏览器。
    # 走查 R3.2：内容随滚动追加 ⇒ 必须用浏览器，并产出滚动动作。
    # 滚动是**唯一**被自动生成的交互：不改服务端状态、不需要选择器、轮数有界；
    # 点击/表单/iframe 一律只给告警并指向 record-actions（见 _interaction_advice）。
    use_browser = force_browser or rendered or scroll_rounds > 0
    if use_browser:
        source_kind = "browser"
    elif analysis.page_type == "detail":
        source_kind = "static_html"
    elif item_selector or analysis.pagination:
        source_kind = "crawl"  # 需要跟随链接（翻页 / 详情页）
    else:
        source_kind = "static_html"

    config: dict[str, Any] = {
        "project": {"name": project_name},
        "source": {"kind": source_kind, "seeds": [url]},
        "crawl": {"max_pages": 200},
        "http": {"user_agent": user_agent("+bot"), "respect_robots": True},
        "extract": {"mode": "html", "fields": fields_dict},
        "outputs": {"jsonl": True, "csv": True, "xlsx": True},
    }
    if analysis.page_type == "detail" and any(field.get("record_grain") == "detail" for field in analysis.fields):
        config["extract"]["record_grain"] = "detail"
        config["crawl"]["max_pages"] = 1
    # 走查 R5.2：分页信号此前**只**用来决定 `source_kind`，检测结果被丢掉 ——
    # 用户拿到 `source.kind: crawl` 却没有任何翻页配置，以为"翻页被处理了"。
    # 现在翻译成 `core/pagination.py` 契约里的形状写进配置；翻译不了时**不写假配置**，只给建议。
    pagination, pagination_notes = _pagination_config(html, analysis.pagination)
    if pagination:
        config["source"]["pagination"] = pagination
    if analysis.pagination and analysis.pagination.get("type") == "next_link":
        config["source"]["follow_xpath"] = str(analysis.pagination.get("xpath") or "")
        # ★ 2026-11-26 修正：下一页链上**每一页都比上一页深一层**，而 `crawl.max_depth`
        #   默认是 3 ⇒ 只写 follow_xpath 而不抬深度，翻页会在第 4 页**静默停住**，
        #   而 `crawl.max_pages` 还写着 30/200。实测（books.toscrape.com）：
        #   同一条配置只改 max_depth（3→50），交付从 4 页 80 条变成 30 页 600 条。
        #   本项目自己的 `templates/generic/next_link_pagination.yaml` 早就写成
        #   `max_depth: 100` —— 生成器没跟上这个口径。
        #   上界取配置校验的 0–100（`core/config.py`），与页数预算取小者：
        #   深度不需要超过"最多能翻多少页"。
        budget = int(config["crawl"].get("max_pages") or 0)
        config["crawl"]["max_depth"] = max(0, min(100, max(budget, 1)))
    if "链接地址" in fields_dict:
        config["extract"]["deduplicate_by"] = ["链接地址"]
    if advisories is not None:
        advisories.extend(pagination_notes)
    if use_browser:
        actions: list[dict[str, Any]] = []
        if scroll_rounds > 0:
            actions = [
                {"action": "wait_ms", "value": "1500"},
                {"action": "scroll_bottom", "times": scroll_rounds, "pause_ms": "1000"},
            ]
        config["browser"] = {"engine": "playwright", "headless": True}
        if actions:
            config["browser"]["actions"] = actions
    if item_selector:
        config["extract"]["item_selector"] = item_selector

    _check_verified(config, html)
    return config


# ── CLI ────────────────────────────────────────────────────────────────

def align_requested_fields(
    fields: dict[str, Any], requested: list[str], multiple: list[str],
) -> dict[str, Any]:
    # ★ 2026-11-26 扩充：需求里的**同义说法**不等于「字段不存在」。
    #   实测事故：用户说「抓取所有书的书名、价格和评分」，站点生成的字段叫「标题」，
    #   于是整条命令以 `需求字段没有可靠规则：书名` 失败 —— 而 `书名` 就是 `标题`。
    #   这条错误说的是「你说的东西页面上没有」，实际是「我们不认识这个说法」。
    #   ★ 但**不能**改成"找不到就忽略"：那会把用户要的字段静默丢掉（既有契约
    #   `test_missing_goal_fields_are_rejected_before_success` 守着这一点）。
    #   所以只扩同义词表，真正的缺字段仍然报错。
    aliases = {
        # 「国家名称」这类：表格站把主列叫「标题」。实测 scrapethissite 国家表
        # 生成的字段是 {标题, 首都, 人口, 面积}，而需求写「国家名称」⇒ 整条失败。
        # 2026-11-26: HTML 表格的表头常是英文原标题（Team Name / Year / Wins），
        #   而需求写中文。与前面「中文说法 → 英文原键」同一条思路。
        "球队名称": ("Team Name", "队名", "名称", "标题"),
        "队名": ("Team Name", "队名", "名称"),
        "年份": ("Year", "年份", "日期"),
        "胜场": ("Wins", "胜场"),
        "负场": ("Losses", "负场"),
        "进球": ("Goals For (GF)", "GF", "进球"),
        "失球": ("Goals Against (GA)", "GA", "失球"),
        "国家名称": ("国家", "名称", "标题"),
        "国名": ("国家", "名称", "标题"),
        "书名": ("标题", "名称"),
        "商品名": ("标题", "名称"),
        "名称": ("名称", "标题"),
        "标题": ("标题", "名称"),
        # 2026-11-26: 评论类需求的常见说法（web-scraping.dev/reviews 实测）
        "评论内容": ("正文", "内容", "评论"),
        "评论": ("正文", "内容", "评论"),
        "评价内容": ("正文", "内容"),
        "语录": ("正文", "内容"),
        "引文": ("正文", "内容"),
        "名言": ("正文", "内容"),
        "正文": ("正文", "内容"),
        "内容": ("正文", "内容"),
        "售价": ("价格",),
        "价钱": ("价格",),
        "单价": ("价格",),
        "星级": ("评分",),
        "打分": ("评分",),
        "作家": ("作者",),
        "著者": ("作者",),
        "分类": ("标签", "分类"),
        "类别": ("标签", "分类"),
        "标签": ("标签", "分类"),
        "库存": ("库存状态",),
        # 2026-11-26: 「链接」有两种合理落点 —— 详情页 URL（链接地址）或锚文本
        #   （链接文本）。只映射前者时，纯文本链接列的页面会整条失败。
        "链接": ("链接地址", "链接文本"),
        "网址": ("链接地址", "链接文本"),
        "url": ("链接地址", "链接文本"),
        "封面": ("图片地址",),
        "图片": ("图片地址",),
        # ★ 2026-11-26（公开 API 清单实测）：JSON 载荷的字段名常是**英文原键**
        #   （`first_name` / `last_name` / `email`），而需求写中文。实测 regres：
        #   站点字段 {编号, 邮箱, first_name, last_name, avatar}，需求「用户姓名」⇒ 整条失败。
        #   这里只补"中文说法 → 英文原键"这一层，键名本身不改（下游按原键读取）。
        "姓名": ("姓名", "name", "full_name", "first_name", "名称"),
        "全名": ("full_name", "姓名", "name", "first_name"),
        "用户名": ("username", "name", "姓名"),
        "邮箱": ("email", "邮箱"),
        "邮件": ("email", "邮箱"),
        "头像": ("avatar", "图片地址"),
        "电话": ("phone", "电话"),
        "手机": ("phone", "电话"),
        "编号": ("id", "编号"),
        "ID": ("id", "编号"),
        "详情链接": ("链接地址", "url", "链接"),
        "详情页": ("链接地址", "url", "链接"),
    }
    def _candidates(name: str) -> tuple[str, ...]:
        """先查同义词表；查不到再按**语义后缀**退一次。

        ★ 2026-11-26：只顾着枚举同义词是治不完的 —— 用户会说「**宝可梦**名称」
        「**用户**姓名」「**商品**详情链接」，前缀是站点专名，千变万化。
        真正稳定的是**后缀**（名称 / 姓名 / 链接 / 价格 …）。
        所以表里查不到时，按后缀再试一次；仍查不到才算"真的没有"。
        这不会退化成"找不到就忽略" —— 见文件里那条既有契约。
        """
        known = aliases.get(name)
        if known:
            return known
        suffix_rules = (
            ("名称", ("名称", "标题")),
            ("姓名", ("姓名", "name", "full_name", "first_name", "名称")),
            ("链接", ("链接地址", "链接", "url")),
            ("价格", ("价格",)),
            ("评分", ("评分",)),
            ("作者", ("作者",)),
            ("日期", ("日期",)),
            ("标题", ("标题", "名称")),
            ("邮箱", ("email", "邮箱")),
        )
        for suffix, targets in suffix_rules:
            if name.endswith(suffix):
                return (*targets, name)
        return (name,)

    selected: dict[str, Any] = {}
    missing = []
    for name in requested:
        match = next((candidate for candidate in _candidates(name) if candidate in fields), None)
        if match is None:
            missing.append(name)
            continue
        selected[name] = dict(fields[match])
        selected[name]["required"] = True
        if name in multiple:
            selected[name]["all"] = True
            selected[name].pop("join", None)
    if missing:
        # 2026-11-26：别名与后缀都不中时，**再按相似度试一次**。
        #   实测：用户说「卡片标题」，页面只有「正文」；说「名字」，页面只有「内容_span」。
        #   这类跨语义的说法靠枚举同义词永远治不完。用一次相似度推荐，
        #   命中就采用并**明确写出替换成了哪个**（不静默替换）。
        fuzzy_hits: dict[str, str] = {}
        still_missing: list[str] = []
        for name in missing:
            guess = suggest_closest_field(name, list(fields))
            if guess:
                fuzzy_hits[name] = guess
            else:
                still_missing.append(name)
        for name, guess in fuzzy_hits.items():
            selected[name] = dict(fields[guess])
            selected[name]["required"] = True
            selected[name]["_matched_field"] = guess
            if name in multiple:
                selected[name]["all"] = True
                selected[name].pop("join", None)
        if still_missing:
            available = "、".join(sorted(fields))
            raise ValueError(
                "需求字段没有可靠规则：" + "、".join(still_missing)
                + f"（这个页面能取到的字段是：{available}；请改成其中之一，或去掉该项）"
            )
    return selected or fields


def apply_task_goal(config: dict[str, Any], settings: dict[str, Any]) -> None:
    if settings.get("unsupported"):
        raise ValueError("需求仍有未支持的自动操作：" + "、".join(settings["unsupported"]))
    config["crawl"]["max_pages"] = int(settings["max_pages"])
    config["extract"]["fields"] = align_requested_fields(
        config["extract"]["fields"], list(settings.get("fields", [])),
        list(settings.get("multi_value_fields", [])),
    )
    # 2026-11-26：字段名被**相似度匹配**替换过时要显式说出来。
    #   静默改用户的话等于撒谎 —— 用户以为抓的是「卡片标题」，实际抓的是「正文」。
    substitutions = {
        name: rule.get("_matched_field")
        for name, rule in (config["extract"].get("fields") or {}).items()
        if isinstance(rule, dict) and rule.get("_matched_field")
    }
    for rule in (config["extract"].get("fields") or {}).values():
        if isinstance(rule, dict):
            rule.pop("_matched_field", None)   # 契约里不许留内部键
    if not all(name in config["extract"]["fields"] for name in config["extract"].get("deduplicate_by", [])):
        config["extract"].pop("deduplicate_by", None)
    config["outputs"] = {key: key in {"jsonl" if item == "json" else item for item in settings["output_formats"]}
                         for key in ("xlsx", "csv", "jsonl")}
    # 2026-11-26：相似度替换要落在**最终**的 config["task"] 里（上面那行会整体覆盖）。
    config["task"] = {
        "requested_fields": list(settings.get("fields", [])),
        "multi_value_fields": list(settings.get("multi_value_fields", [])),
    }
    if substitutions:
        config["task"]["field_substitutions"] = substitutions


def _analysis_browser_reason(result: Any) -> str:
    from omnicrawler.extraction.extractors import decode_body
    from omnicrawler.fetching.routing import needs_browser

    browser, reason = needs_browser(result)
    if browser:
        return reason
    if "html" not in result.content_type and result.content_type:
        return ""
    html = decode_body(result)
    if not re.search(r"<script\b", html, re.I):
        return ""
    analysis = analyze_page(html, result.final_url)
    top = analysis.patterns[0] if analysis.patterns else None
    if analysis.page_type not in {"detail", "table"} and (
        top is None or top.count < 3 or _is_chrome_path(top.css_path)
    ):
        return "静态内容未形成可用业务列表，存在脚本，需浏览器补充确认"
    return ""


def _build_probe_config(url: str) -> Any:
    """为自动分析构造一份最小可用的 AppConfig（不写盘、只读）。"""
    from omnicrawler.core.config import AppConfig

    root = Path.cwd()
    raw: dict[str, Any] = {
        "project": {"name": "auto_analyze", "workspace": "work/auto_analyze"},
        "source": {"kind": "browser", "seeds": [url]},
        "crawl": {"max_pages": 1},
        "http": {"user_agent": user_agent("+bot"), "respect_robots": True, "delay_seconds": 0.2},
        "browser": {"engine": "playwright", "headless": True},
        "extract": {"mode": "html", "fields": {}},
        "outputs": {"jsonl": False, "csv": False, "xlsx": False},
    }
    return AppConfig(root / ".omnicrawler-auto-analyze.yaml", root, raw, root / "work" / "auto_analyze")


def _fetch_html(url: str) -> tuple[str, str]:
    """抓取单页供自动分析，返回 ``(文本, content_type)``。

    抓取顺序（2026-09-12 改造：自有栈优先）：
      1. 产品自有 HTTP 栈（``HTTPFetcher``）——快，静态页与 API 首选
      2. 产品自有浏览器栈（``BrowserFetcher`` + Playwright）——渲染 JS

    旧实现**只**依赖可选的第三方 crawl4ai 抓取。它在本机对
    news.ycombinator.com、scrapethissite.com、the-internet.herokuapp.com、
    webscraper.io、demo.opencart.com、pixelscan.net、browserscan.net 等
    站点稳定抛 ACS-GOTO 导航错误，而错误被吞掉：命令退出码 0、不产出任何文件、
    用户只看到一屏第三方库的源码堆栈。自有栈是产品承诺的"本地自主"路径，
    理应是一等公民而非依赖外部库。
    """
    from omnicrawler.extraction.extractors import decode_body
    from omnicrawler.fetching.page_probe import fetch_analysis_page

    result, _rendered, error = fetch_analysis_page(url, browser_reason=_analysis_browser_reason)
    if error:
        raise RuntimeError(error)
    if result.status >= 400:
        raise RuntimeError(f"页面获取失败：HTTP {result.status}")
    return decode_body(result), result.content_type


def _fetch_rendered(url: str) -> str:
    """只用浏览器栈渲染抓取一页，返回 HTML；失败返回空串。

    静态 HTML 里没有可用列表时（SPA / JS 渲染站点：scrapeme.live 首页、
    quotes.toscrape.com/js 等）用它拿到渲染后的 DOM 再分析。生成的配置本就是
    ``source.kind=browser``，因此"分析用渲染结果 + 运行用浏览器"两者一致。
    """
    from omnicrawler.extraction.extractors import decode_body
    from omnicrawler.fetching.page_probe import fetch_analysis_page

    result, rendered, error = fetch_analysis_page(url, force_browser=True, browser_reason=_analysis_browser_reason)
    if error:
        raise RuntimeError(error)
    return decode_body(result) if rendered else ""


def main(task_settings: dict[str, Any] | None = None) -> None:
    import argparse
    import sys

    import yaml
    parser = argparse.ArgumentParser(description="智能爬虫 — 自动分析网页结构并生成配置")
    parser.add_argument("input", help="HTML 文件路径 或 URL")
    parser.add_argument("-o", "--output", help="输出 YAML 配置路径")
    parser.add_argument("--url", help="页面原始 URL（用于分页检测，当 input 为文件时提供）")
    parser.add_argument("--json", action="store_true", help="输出完整分析 JSON 而非 YAML")
    parser.add_argument(
        "--always-browser",
        action="store_true",
        help="即使静态 HTML 已足以生成配置，也强制运行期用浏览器抓取（逃生阀）",
    )
    parser.add_argument(
        "--item-path",
        help="显式指定 JSON 记录路径（如 $.results[*]），跳过自动打分；只对 JSON 载荷生效",
    )
    args = parser.parse_args()

    # 获取 HTML：URL 走共享安全分析栈；文件直接读
    initial_rendered = False
    html: str
    url = args.url or ""
    item_path_override = str(getattr(args, "item_path", "") or "")
    if args.input.startswith("http://") or args.input.startswith("https://"):
        url = args.input
        try:
            from omnicrawler.extraction.extractors import decode_body
            from omnicrawler.fetching.page_probe import fetch_analysis_page

            result, initial_rendered, analysis_error = fetch_analysis_page(args.input, browser_reason=_analysis_browser_reason)
            if analysis_error:
                raise RuntimeError(analysis_error)
            if result.status >= 400:
                raise RuntimeError(f"页面获取失败：HTTP {result.status}")
            html = decode_body(result)
            url = result.final_url or url
        except Exception as exc:  # noqa: BLE001 —— 抓取失败必须以非零退出码暴露
            print(f"错误: 无法获取页面内容（{type(exc).__name__}: {exc}）", file=sys.stderr)
            raise SystemExit(2) from exc
        if not html.strip():
            print(
                f"错误: 抓到的页面内容为空（{url}；可能是 JS 动态渲染或反爬拦截）",
                file=sys.stderr,
            )
            raise SystemExit(2)
    else:
        html = Path(args.input).read_text(encoding="utf-8", errors="replace")

    if args.json:
        analysis = analyze_page(html, url)
        output = json.dumps({
            "url": analysis.url, "page_type": analysis.page_type,
            "confidence": analysis.confidence,
            "rendered": initial_rendered,
            "patterns_count": len(analysis.patterns),
            "fields": analysis.fields,
            "pagination": analysis.pagination,
        }, ensure_ascii=False, indent=2)
    else:
        # 静态 HTML 与浏览器渲染各分析一次，取"验证记录数更多"的那份配置。
        # 静态结果弱（无可用配置 / 最像列表的组 <3 项 / 落在 aside·nav·footer）
        # 时才额外付出渲染开销——SPA 与 JS 渲染站点（scrapeme.live 首页、
        # quotes.toscrape.com/js）静态 DOM 里根本没有业务列表。
        best_config: dict[str, Any] | None = None
        best_html = html
        best_records = -1
        # 2026-11-26: 质量排名（有意义字段数 > 填充齐全数 > 记录数），
        #   「记录更多」不再等于「结果更好」。
        best_rank: tuple[int, int, int] = (-1, -1, -1)
        #: 走查 R4.2：只保留**胜出那份**配置的提醒，避免静态/渲染两份候选各报一遍。
        chosen_advisories: list[str] = []

        # 走查 R3.1：`from_render` 决定生成配置的 `source.kind` —— 分析用哪份 HTML，
        # 运行就用哪种抓取方式（静态 HTML 够用 ⇒ static_html，不必启动浏览器）。
        force_browser = bool(getattr(args, "always_browser", False))
        chosen_from_render = False
        # 走查 R3.2：先把交互信号一次性检出（静态 HTML 上判即可），
        # 「内容随滚动追加」直接进配置，其余信号走告警。
        signals = _interaction_signals(html)
        scroll_rounds = max((_SCROLL_ROUNDS.get(name, 0) for name in signals), default=0)

        def _consider(candidate_html: str, *, from_render: bool) -> None:
            nonlocal best_config, best_html, best_records, best_rank, chosen_from_render, chosen_advisories
            local_advisories: list[str] = []
            try:
                candidate = analyze_to_config(
                    candidate_html, url, rendered=from_render,
                    force_browser=force_browser, scroll_rounds=scroll_rounds,
                    item_path_override=item_path_override, advisories=local_advisories,
                )
            except AutoConfigUnverifiedError:
                return
            report_candidate = verify_config(candidate, candidate_html) or {}
            records = int(report_candidate.get("records") or 0)
            # 2026-11-26: 胜出判据从「记录数更多」改成「**质量排名更高**」。
            #   实测 randomuser.me：静态 42 条（字段全是 内容_span）vs 渲染后少量但字段有意义。
            #   按记录数比，渲染结果永远赢不了 —— 而静态那份是纯噪声。
            rank = _result_quality_rank(candidate, report_candidate)
            if rank > best_rank:
                best_config, best_html, best_records = candidate, candidate_html, records
                best_rank = rank
                chosen_from_render = from_render
                chosen_advisories = local_advisories

        _consider(html, from_render=initial_rendered)
        static_analysis = analyze_page(html, url)
        static_top = static_analysis.patterns[0] if static_analysis.patterns else None
        # ★ 2026-11-26：`js_pagination` 也要让静态结果算「弱」。
        #   判据：页面留着一个**空的分页挂载点**（`<div id="static-pagination"></div>`），
        #   说明页码是 JS 拉回来填的 —— 静态 HTML 里只有首屏那几条。
        #   实测 webscraper.io/.../ajax：每页 3 条共 3 页，静态只拿到 3 条却报「足够」。
        weak = (
            best_config is None
            or static_top is None
            or static_top.count < 3
            or _is_chrome_path(static_top.css_path)
            or "js_pagination" in signals
            # 2026-11-26：静态结果「像不像业务数据」也进判据。
            #   零个有意义字段（全是 内容_<tag> 兜底名）⇒ 静态 DOM 里没有业务结构，
            #   必须再试一次渲染；否则用户拿到一列列 内容_span 还以为成功了。
            or not _meaningful_field_names(best_config)
        )
        rendered_html = ""
        if weak and url and not initial_rendered:
            rendered = _fetch_rendered(url)
            if rendered.strip():
                rendered_html = rendered
                before_rank = best_rank
                _consider(rendered, from_render=True)
                if best_rank > before_rank:
                    print(
                        "提示: 静态 HTML 的结构不足以生成可用配置，已改用浏览器渲染结果",
                        file=sys.stderr,
                    )

        if best_config is None:
            print("错误: 自动分析未能产出可用配置", file=sys.stderr)
            for line in _explain_no_config(
                html, static_top=static_top, rendered=rendered_html, signals=signals
            ):
                print(f"  · {line}", file=sys.stderr)
            raise SystemExit(3)
        config, html = best_config, best_html
        # 2026-11-26：给一句**结论**（这站属于哪类、要不要动手）。
        #   原来成功只打印条数与字段名，用户得自己反推「这是静态还是渲染」。
        _verdict, _why = _result_verdict(
            config=config,
            records=best_records,
            signals=signals,
            used_render=chosen_from_render,
        )
        _verdict_line = f"结论: {_verdict}" + (f" —— {_why}" if _why else "")
        if task_settings is not None:
            try:
                apply_task_goal(config, task_settings)
                _check_verified(config, html)
            except (ValueError, AutoConfigUnverifiedError) as exc:
                # B-P2-2：自动配置验证失败（RuntimeError 子类）与参数错误统一转
                # 友好 SystemExit(3)，不再以 Python traceback 退出。
                print(f"错误: {exc}", file=sys.stderr)
                raise SystemExit(3) from exc
        # 走查 R3.1：让用户知道运行期会用哪种抓取方式（这直接决定耗时可否省下浏览器）。
        chosen_kind = str((config.get("source") or {}).get("kind") or "")
        if chosen_kind in {"static_html", "crawl"} and not chosen_from_render:
            print(
                f"提示: 静态 HTML 已足以生成配置，运行期按 {chosen_kind} 抓取（不启动浏览器）；"
                "如判定失手，加 --always-browser 可强制走浏览器",
                file=sys.stderr,
            )
        # 2026-11-26：字段名被相似度替换过时**必须说出来**。
        #   静默改用户的话等于撒谎 —— 用户以为抓「卡片标题」，实际抓「正文」。
        substitutions = ((config.get("task") or {}).get("field_substitutions") or {})
        if isinstance(substitutions, dict) and substitutions:
            pairs = "；".join(f"{user} → {real}" for user, real in substitutions.items())
            print(f"提示: 以下字段名页面上没有完全对应的，已按最接近的取：{pairs}", file=sys.stderr)
        # 走查 R3.2：交互信号必须**说出来** —— 要么已自动配好（滚动），要么告知要手工补录。
        for line in _interaction_advice(
            signals, scroll_rounds=scroll_rounds, iframe_locators=_iframe_locators(html)
        ):
            print(f"提示: {line}", file=sys.stderr)
        # 走查 R4.2：记录路径拿不太准（单对象 / 候选接近）、或有嵌套没展开时，如实说出来。
        for line in chosen_advisories:
            print(f"提示: {line}", file=sys.stderr)
        output = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
        try:
            report = verify_config(config, html)
        except AutoConfigUnverifiedError as exc:
            # B-P2-2：最终验证失败同样转友好 SystemExit(3)，不抛 traceback。
            print(f"错误: {exc}", file=sys.stderr)
            raise SystemExit(3) from exc
        print(
            f"已验证: {report['records']} 条记录 / {report['items']} 个列表项"
            f" / 字段 {report['fields']}",
            file=sys.stderr,
        )
        # 2026-11-26：一句话结论 —— 用户问的就是「这站能不能抓」，
        #   而不是「多少条记录」。放在最后一行，紧挨着结果。
        print(_verdict_line, file=sys.stderr)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()

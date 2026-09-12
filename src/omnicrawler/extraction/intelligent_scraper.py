"""智能爬虫 — 自动分析网页 DOM 结构，推断列表/详情/分页模式，生成字段配置。

核心算法:
    1. 抓取页面 → 构建 DOM 特征树
    2. 检测重复模式 → 识别列表项（商品卡片、文章条目等）
    3. 分析子元素 → 推断字段名（标题、价格、日期、链接等）
    4. 检测分页 → 识别"下一页"按钮或 URL 模式
    5. 输出 → OmniCrawler field_spec + 配置

无需任何人工标注，一个 URL 即可自动生成完整采集配置。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.utils import user_agent

# ── DOM 特征提取 ──────────────────────────────────────────────────────

@dataclass
class DOMNode:
    """轻量 DOM 节点表示。"""
    tag: str = ""
    classes: list[str] = field(default_factory=list)
    id_: str = ""
    text: str = ""
    href: str = ""
    src: str = ""
    depth: int = 0
    child_count: int = 0
    children_tags: list[str] = field(default_factory=list)
    children_keys: list[str] = field(default_factory=list)
    parent_tag: str = ""
    parent_key: str = ""
    itemprop: str = ""
    xpath: str = ""
    css_path: str = ""
    is_link: bool = False
    is_image: bool = False
    is_heading: bool = False
    is_list_item: bool = False


def _parse_dom(html: str, *, max_nodes: int = 5000) -> list[DOMNode]:
    """解析 HTML 为 DOMNode 列表。"""
    try:
        from lxml import html as lxml_html
    except ImportError:
        raise RuntimeError("lxml 是必须的依赖")

    try:
        root = lxml_html.fromstring(html)
    except Exception:
        return []

    nodes: list[DOMNode] = []
    _walk(root, nodes, depth=0, max_nodes=max_nodes)
    return nodes


def _tag_name(element: Any) -> str:
    """返回元素的小写标签名；非元素节点返回空串。

    lxml 对注释 / 处理指令节点把 ``.tag`` 暴露为 Cython 函数
    （``etree.Comment`` / ``etree.PI``）而非字符串，直接 ``.tag.lower()``
    会抛 ``AttributeError: '_cython_3_2_4.cython_function_or_method'
    object has no attribute 'lower'``。真实站点几乎都含 HTML 注释
    （``<!-- ... -->``），因此必须按类型判定而不是只判 ``hasattr``。
    （同包 ``field_designer`` 一直用的是 ``isinstance(element.tag, str)`` 口径。）
    """
    tag = getattr(element, "tag", None)
    return tag.lower() if isinstance(tag, str) else ""


def _walk(element: Any, nodes: list[DOMNode], depth: int, max_nodes: int) -> None:
    if len(nodes) >= max_nodes:
        return
    tag = _tag_name(element)
    if not tag or tag in {"head", "script", "style", "noscript", "meta", "link"}:
        # 递归子元素（跳过这些标签本身但处理其可见子节点 —— 实际上 script/style 不应深入）
        if tag in {"head", "script", "style", "noscript"}:
            return
        for child in element:
            _walk(child, nodes, depth, max_nodes)
        return

    text = (element.text_content() or "").strip()[:500] if hasattr(element, "text_content") else ""
    classes = list(element.classes) if hasattr(element, "classes") else []
    id_ = element.get("id", "") if hasattr(element, "get") else ""

    href = element.get("href", "") if hasattr(element, "get") else ""
    src = element.get("src", "") if hasattr(element, "get") else ""
    itemprop = str(element.get("itemprop", "") or "").strip() if hasattr(element, "get") else ""

    children = [c for c in element if _tag_name(c)] if hasattr(element, "__iter__") else []
    child_tags = [_tag_name(c) for c in children]
    # 子元素的 (标签, 首类名) 签名——判断"重复单元结构是否丰富"要比标签名细一档：
    # 商品卡片的 缩略图/描述/按钮 都是 div，只数标签名会把卡片判成"结构贫瘠"。
    child_keys: list[str] = []
    for child in children:
        child_classes = list(child.classes) if hasattr(child, "classes") else []
        child_keys.append(f"{_tag_name(child)}.{child_classes[0]}" if child_classes else _tag_name(child))

    # 生成 CSS 路径
    css = _build_css(element)

    parent = element.getparent() if hasattr(element, "getparent") else None
    parent_classes = list(parent.classes) if parent is not None and hasattr(parent, "classes") else []
    parent_key = _tag_name(parent) if parent is not None else ""
    if parent_classes:
        parent_key = f"{parent_key}.{parent_classes[0]}"
    node = DOMNode(
        tag=tag, classes=classes, id_=id_, text=text,
        href=href, src=src, depth=depth,
        child_count=len(children), children_tags=child_tags,
        children_keys=child_keys,
        parent_tag=_tag_name(parent) if parent is not None else "",
        parent_key=parent_key,
        itemprop=itemprop,
        xpath="",  # 调用方自行生成
        css_path=css,
        is_link=bool(href and tag == "a"),
        is_image=bool(src or tag == "img"),
        is_heading=tag in {"h1", "h2", "h3", "h4", "h5", "h6"},
        is_list_item=tag in {"li", "option"},
    )
    nodes.append(node)

    for child in children:
        _walk(child, nodes, depth + 1, max_nodes)


def _build_css(element: Any) -> str:
    """节点 → 自文档根起的绝对 CSS 路径。

    2026-09-12：去掉旧的"最多 5 段"截断。截断让同一元素在不同深度下拿到
    **不对齐的路径前缀**（深层节点丢掉开头的 ``body``），于是"某节点是否位于
    某 item 内部"这类前缀判断会静默失败——quotes 页面里 ``div.quote > span > small``
    的作者字段就是这么丢掉的（作者是用户明确要的字段之一）。
    路径长度由页面真实嵌套深度决定，另设 32 段安全上限防畸形文档。
    """
    parts: list[str] = []
    current = element
    while current is not None and _tag_name(current) not in {"html", ""}:
        tag = _tag_name(current)
        classes = list(current.classes) if hasattr(current, "classes") else []
        css = tag
        for cls in classes[:2]:
            # 非标识符类名（如 "w-1/2"）改用属性选择器，否则 cssselect 编译失败
            css += f".{cls}" if _CSS_IDENT.match(cls) else f'[class~="{cls}"]'
        parts.insert(0, css)
        current = current.getparent() if hasattr(current, "getparent") else None
        if len(parts) > 32:
            break
    return " > ".join(parts)


# ── 重复模式检测 ──────────────────────────────────────────────────────

@dataclass
class RepeatingPattern:
    """检测到的重复模式（列表）。"""
    css_path: str = ""
    xpath: str = ""
    count: int = 0
    sample_texts: list[str] = field(default_factory=list)
    child_structure: list[str] = field(default_factory=list)  # 子元素 tag 签名
    depth: int = 0
    score: float = 0.0


def _distinct_child_tags(group: list[DOMNode]) -> int:
    """重复单元的"结构丰富度"：不同直接子元素签名数（封顶 4）。

    用 ``(标签, 首类名)`` 而非单纯标签名——商品卡片的 缩略图/描述/按钮 都是
    ``div``，只数标签名会把卡片误判成"结构贫瘠"，从而输给更深更密的叶子组。
    """
    seen: set[str] = set()
    for node in group:
        keys = node.children_keys or node.children_tags
        for key in keys:
            if key:
                seen.add(key)
                if len(seen) >= 4:
                    return 4
    return len(seen)


_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# 页面"装饰"容器：侧边栏小工具、导航标签、页脚链接常自身重复，
# 且每组恰好带一个标题，会骗过卡片加分（scrapeme.live 首页侧边栏 widget 组
# 曾压过商品网格）。列表抽取默认应落在主内容区。
_CHROME_TOKENS = frozenset({"aside", "nav", "footer"})


def _is_chrome_path(css_path: str) -> bool:
    """CSS 路径是否位于 aside / nav / footer 等装饰性容器内。"""
    for segment in css_path.split(">"):
        parts = [part for part in segment.strip().lower().split(".") if part]
        if parts and (parts[0] in _CHROME_TOKENS or any(p in _CHROME_TOKENS for p in parts[1:])):
            return True
    return False



def _is_heading_key(key: str) -> bool:
    """子元素签名键（``h3.country-name`` 或 ``h3``）是否指向标题标签。"""
    return key.split(".", 1)[0].lower() in _HEADING_TAGS


def _node_signature(node: DOMNode) -> list[str]:
    """与 ``detect_repeating_patterns`` 分组时一致的子元素签名。"""
    keys = node.children_keys or node.children_tags
    return list(keys[:5]) if keys else ["leaf"]


def _items_by_signature(children: list[DOMNode], child_structure: list[str]) -> list[DOMNode]:
    """从容器子元素里取出与模式签名一致的重复分组。

    容器下常混有结构不同的兄弟（页脚 / 分页 / 标题栏）。旧实现把全部兄弟当成
    item，``item_selector`` 便退化成"容器 > 第一个子元素"，抽取器随后在单个子元素
    内部找字段 → 全部落空、0 条记录（scrapethissite 国家列表即此形态：容器
    ``div.col-md-4.country`` 下 250 个 ``h3`` 与 250 个 ``div.country-info`` 各成一组，
    旧实现取 items[0]=h3 生成 ``... > h3.country-name`` 作 item 选择器）。
    """
    want = list(child_structure)
    if not want or want == ["leaf"]:
        return children
    return [child for child in children if _node_signature(child) == want]

def _path_contains(descendant: str, ancestor: str) -> bool:
    """``descendant`` 的 CSS 段序列是否包含 ``ancestor`` 的连续段序列。"""
    a = [seg.strip() for seg in descendant.split(">") if seg.strip()]
    b = [seg.strip() for seg in ancestor.split(">") if seg.strip()]
    if not b or len(a) <= len(b):
        return False
    return any(a[i : i + len(b)] == b for i in range(len(a) - len(b) + 1))


def _suppress_nested(patterns: list[RepeatingPattern]) -> list[RepeatingPattern]:
    """丢掉"完全嵌在更高分候选的 item 内部"的候选。

    这类候选是列表项内部的子结构（标签链接、评分星、页脚小图标），
    把它们当列表会产出 0 条记录或垃圾字段。
    """
    kept: list[RepeatingPattern] = []
    for pattern in patterns:
        nested = any(
            pattern.depth > better.depth and _path_contains(pattern.css_path, better.css_path)
            for better in kept
        )
        if not nested:
            kept.append(pattern)
    return kept


def detect_repeating_patterns(nodes: list[DOMNode]) -> list[RepeatingPattern]:
    """检测页面中的重复模式（列表项）。

    打分口径（2026-09-12 重写，替换旧口径）：
      旧口径直接按 ``score * count`` 排序——等于**奖励元素个数**。后果是更深、
      更密的叶子组（分页页码、标签链接、面包屑）永远压过真正的列表容器。
      实测 quotes.toscrape：标签链接组 score×count=38，真容器（``div.col-md-8``，
      item 为 ``div.quote``）仅 10 → 自动配置去抓标签链接、跑出 **0 条记录**；
      web-scraping.dev 同理，分页组（5.95）压过商品列表（3.75）。

    新口径按"这组元素像不像业务列表"打分，三项加权：
      * **单元结构丰富度**：item 内部不同子标签数 —— 越高越像卡片（0.40）
      * **正文覆盖率**：该组文本量 / 页面可见文本量 —— 排除导航/分页/页脚（0.40）
      * **文本多样性**：不同文本占比 —— 排除"同一模板文案反复出现"（0.20）
    并加**嵌套抑制**（见 ``_suppress_nested``）。
    """
    # 按 CSS 父路径分组
    parent_groups: dict[str, list[DOMNode]] = {}
    for node in nodes:
        if node.depth < 2:
            continue
        # 提取父级 CSS 路径
        parent_css = _parent_css(node.css_path)
        if parent_css:
            parent_groups.setdefault(parent_css, []).append(node)

    page_text_total = sum(len(n.text) for n in nodes) or 1

    patterns: list[RepeatingPattern] = []
    for parent_css, children in parent_groups.items():
        if len(children) < 3:  # 至少 3 个同类元素
            continue

        # 按子元素结构签名分组（标签 + 首类名，比纯标签名更细）
        signature_groups: dict[str, list[DOMNode]] = {}
        for child in children:
            keys = child.children_keys or child.children_tags
            sig = "|".join(keys[:5]) if keys else "leaf"
            signature_groups.setdefault(sig, []).append(child)

        for sig, group in signature_groups.items():
            if len(group) < 3:
                continue
            texts = [n.text for n in group if n.text]
            unique_texts = len(set(texts))
            diversity = unique_texts / len(group)

            richness = _distinct_child_tags(group)
            coverage = min(1.0, sum(len(n.text) for n in group) / page_text_total)

            score = (
                0.40 * min(1.0, richness / 3.0)
                + 0.40 * min(1.0, coverage / 0.5)
                + 0.20 * diversity
            )
            # 卡片判别加分：真正的列表项通常自带标题（h1-h6 直接子元素），
            # 而"卡片内部的信息块"没有。实测 scrapethissite 国家页：国家卡
            # （含 h3.country-name）与卡内 info 块（strong/span）基础分差 <0.01，
            # 加分后卡片级模式稳定胜出，item_selector 才落在卡上而非卡内某节点。
            if any(
                _is_heading_key(key)
                for node in group
                for key in (node.children_keys or node.children_tags)
            ):
                score += 0.15
            if _is_chrome_path(parent_css):
                score -= 0.20


            patterns.append(RepeatingPattern(
                css_path=parent_css,
                count=len(group),
                sample_texts=texts[:5],
                child_structure=sig.split("|") if sig != "leaf" else [],
                depth=group[0].depth if group else 0,
                score=score,
            ))

    # 按分数降序排列（同分时保留文档顺序，稳定排序即可）
    patterns.sort(key=lambda p: p.score, reverse=True)
    return _suppress_nested(patterns)[:10]


def _parent_css(css_path: str) -> str:
    parts = [p.strip() for p in css_path.split(">")]
    return " > ".join(parts[:-1]) if len(parts) > 1 else css_path


# ── 字段推断 ──────────────────────────────────────────────────────────

# 字段名推断规则：(类名正则, 允许标签 "a|b" 或空, 字段名)
# 顺序即优先级——越靠前的语义越强，先匹配先返回。
_FIELD_RULES: list[tuple[str, str, str]] = [
    (r"(price|价钱|价格|售价|金额|￥|\$)", "span|div|strong|b", "价格"),
    (r"(author|作者|发布者|writer|byline)", "span|a|div|small|p", "作者"),
    (r"(date|time|日期|时间|published|pubdate)", "time|span|div|a|p", "日期"),
    (r"(rating|star|评分|评价|score)", "span|div|i|p", "评分"),
    (r"(title|标题|name|名称|heading|headline)", "a|h[1-6]|span|div|p", "标题"),
    (r"(desc|description|简介|摘要|描述|summary|excerpt)", "p|div|span|small", "描述"),
    (r"(img|image|photo|图片|缩略图|thumb)", "img", "图片地址"),
    (r"(tag|category|标签|分类|类型|type|genre)", "span|a|div|li", "分类"),
    (r"(location|address|地址|位置|地区)", "span|div|a|p", "地址"),
    (r"(phone|tel|电话|手机|联系)", "span|a|div|p", "电话"),
    (r"(email|e-mail|邮箱|邮件)", "span|a|div|p", "邮箱"),
    (r"(sku|product[-_]?id|编号|货号|代码)", "span|div|p|small", "编号"),
    (r"(stock|availability|库存|状态|有无)", "span|div|p|small", "库存状态"),
    (r"(population|人口|居民)", "span|div|p|strong", "人口"),
    (r"(area|面积|占地)", "span|div|p|strong", "面积"),
    (r"(capital|首都|省会)", "span|div|p|strong", "首都"),
    (r"(text|body|content|正文|内容|article[-_]?body)", "span|p|div|small|article", "正文"),
]


_CSS_IDENT = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")

# schema.org itemprop → 中文业务字段名。真实站点（尤其电商/媒体）大量使用
# microdata，语义比 class 名可靠得多——优先于 class 猜测。
_ITEMPROP_NAMES: dict[str, str] = {
    "name": "名称",
    "headline": "标题",
    "title": "标题",
    "price": "价格",
    "lowprice": "价格",
    "highprice": "价格",
    "author": "作者",
    "creator": "作者",
    "description": "描述",
    "articlebody": "正文",
    "text": "正文",
    "image": "图片地址",
    "thumbnailurl": "图片地址",
    "url": "链接地址",
    "datepublished": "发布日期",
    "dateposted": "发布日期",
    "datecreated": "创建日期",
    "sku": "编号",
    "productid": "编号",
    "brand": "品牌",
    "category": "分类",
    "aggregaterating": "评分",
    "ratingvalue": "评分",
    "availability": "库存状态",
    "email": "邮箱",
    "telephone": "电话",
    "telephone_number": "电话",
    "address": "地址",
    "jobtitle": "职位",
    "company": "公司",
}


def _css_token(node: DOMNode) -> str:
    """节点 → 合法 CSS 选择器片段（tag + 最多两个 class）。

    class 名可能含数字/冒号等非标识符字符（``col-md-8`` 合法，``w-1/2`` 不合法），
    直接拼 ``.cls`` 会让 cssselect 编译失败——这种 class 改用 ``[class~="..."]``。
    """
    parts = [node.tag] if node.tag else []
    for cls in node.classes[:2]:
        if _CSS_IDENT.match(cls):
            parts.append(f".{cls}")
        else:
            parts.append(f'[class~="{cls}"]')
    return "".join(parts)


def _relative_segments(desc: DOMNode, item_css: str) -> list[str]:
    """返回 desc 相对 item 的 CSS 段序列（item 自身不含在内）。"""
    prefix = f"{item_css} > "
    tail = desc.css_path[len(prefix):] if desc.css_path.startswith(prefix) else ""
    segs = [s.strip() for s in tail.split(">") if s.strip()]
    if segs:
        return segs
    all_segs = [s.strip() for s in desc.css_path.split(">") if s.strip()]
    return all_segs[-1:] if all_segs else []


def _rel_selector(desc: DOMNode, item_css: str, depth: int) -> str:
    segs = _relative_segments(desc, item_css)
    return " > ".join(segs[-depth:]) if segs else ""


def _unique_relative_selector(
    desc: DOMNode, item_css: str, item_descendants: list[DOMNode]
) -> str:
    """在 item 内选一个**唯一的**相对选择器。

    抽取器把字段选择器当作 item 内的相对选择来跑，所以必须相对（旧实现输出的是
    绝对全路径 + ``> *``，在 item 内根本匹配不到 → 必然 0 记录），并且要唯一
    （否则一条 item 内多个匹配会拼成数组）。逐级加长相对路径直到唯一。
    """
    for depth in (1, 2, 3):
        candidate = _rel_selector(desc, item_css, depth)
        if not candidate:
            continue
        hits = sum(1 for s in item_descendants if _rel_selector(s, item_css, depth) == candidate)
        if hits == 1:
            return candidate
    return _rel_selector(desc, item_css, 2) or _rel_selector(desc, item_css, 1)


def _field_key(node: DOMNode) -> str:
    """字段聚合键：优先 itemprop，其次 tag + class。

    无类名元素额外带上父级签名——``<span><a>(about)</a></span>`` 与
    ``<h3><a>标题</a></h3>`` 里的 ``a`` 都是"无类名链接"，只按 ``tag`` 聚合会把
    完全不同的角色并成一组，恒定文案过滤（见 ``_infer_item_fields``）也就跟着失效。
    """
    if node.itemprop:
        return f"itemprop:{node.itemprop.lower()}"
    if node.classes:
        return f"tag:{node.tag}:{'.'.join(node.classes[:2])}"
    return f"tag:{node.tag}:@{node.parent_key or '?'}"


def _itemprop_field_name(itemprop: str) -> str:
    key = itemprop.lower()
    if key in _ITEMPROP_NAMES:
        return _ITEMPROP_NAMES[key]
    return f"字段_{itemprop}"


def _infer_item_fields(
    container_css: str,
    items: list[DOMNode],
    nodes: list[DOMNode],
    *,
    max_fields: int = 6,
) -> list[dict[str, Any]]:
    """容器模式：item 内部找业务字段，输出 item 内相对选择器。"""
    sample = items[:12]
    by_key: dict[str, dict[str, Any]] = {}
    for item in sample:
        descendants = [
            n for n in nodes
            if n.css_path.startswith(item.css_path + " > ") and n.depth > item.depth and (n.text or n.is_image)
        ]
        for candidate in descendants:
            key = _field_key(candidate)
            slot = by_key.setdefault(
                key,
                {"node": candidate, "texts": [], "items": 0, "attr": None},
            )
            slot["items"] += 1
            slot["texts"].append(candidate.text[:200])
            if candidate.itemprop:
                slot["attr"] = "content" if candidate.tag == "meta" else None
            elif candidate.is_image:
                slot["attr"] = "src"
            elif candidate.is_link and not candidate.text:
                slot["attr"] = "href"

    total = len(sample) or 1

    # 去掉"聚合型"候选：自身包含其它候选节点的元素。它们的文本会把子字段
    # 拼成一整块（quotes 的 `span` = "by Marilyn Monroe (about)"），
    # 留下它们等于用垃圾字段淹没真正要的字段。
    def _is_ancestor(ancestor: DOMNode, child: DOMNode) -> bool:
        return bool(ancestor.css_path) and ancestor.css_path != child.css_path and (
            child.css_path.startswith(ancestor.css_path + " > ")
        )

    all_nodes = [slot["node"] for slot in by_key.values()]
    aggregate_keys = {
        key for key, slot in by_key.items()
        if any(_is_ancestor(slot["node"], other) for other in all_nodes if other is not slot["node"])
    }
    if len(aggregate_keys) < len(by_key):
        for key in aggregate_keys:
            by_key.pop(key, None)

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for key, slot in by_key.items():
        presence = slot["items"] / total
        if presence < 0.6:  # 必须在多数 item 里都出现，否则不是稳定字段
            continue
        texts = slot["texts"]
        # 恒定文案（每件 item 完全相同，且出现 3 次以上）是模板噪声，
        # 不是业务字段——如页码 "(about)"、"Accepted usernames are:"。
        if slot["attr"] is None and len(texts) >= 3 and len(set(texts)) == 1:
            continue
        avg_len = sum(len(t) for t in texts) / max(1, len(texts))
        ranked.append((presence * 0.6 + min(1.0, avg_len / 200) * 0.4, key, slot))
    ranked.sort(key=lambda x: x[0], reverse=True)

    fields: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for _score, key, slot in ranked[:max_fields]:
        desc: DOMNode = slot["node"]
        # desc 可能深藏在 item 内部，定位它真正所属的 item 再算相对路径
        owner = next(
            (it for it in sample if desc.css_path.startswith(it.css_path + " > ")),
            None,
        )
        item_css = owner.css_path if owner is not None else _parent_css(desc.css_path)
        item_descendants = (
            [
                n for n in nodes
                if n.css_path.startswith(item_css + " > ") and n.depth > owner.depth
            ]
            if owner is not None
            else [desc]
        ) or [desc]

        if key.startswith("itemprop:"):
            name = _itemprop_field_name(key.split(":", 1)[1])
            selector = f'[itemprop="{key.split(":", 1)[1]}"]'
        else:
            parts = key.split(":")
            classes_hint = parts[2] if len(parts) > 2 and not parts[2].startswith("@") else ""
            name = _classify_field(parts[1] if len(parts) > 1 else "", classes_hint, slot["texts"][:3])
            selector = _unique_relative_selector(desc, item_css, item_descendants)
            # `h3 > a` / `h2 > a` 是极常见的"标题即链接"结构：链接本身没有语义
            # 类名，但父级标题标签已经把语义说清楚了。
            if desc.tag == "a" and desc.parent_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                name = "标题"

        if not name or not selector:
            continue
        if name in used_names:
            name = f"{name}_{len(used_names) + 1}"
        used_names.add(name)

        rule: dict[str, Any] = {
            "name": name,
            "selector": selector,
            "attribute": slot["attr"] or "text",
            "desc": f"自动推断: {name}",
            "examples": slot["texts"][:3],
        }
        fields.append(rule)

    if not fields:
        return []
    # item 选择器用"容器 + item 自身签名"，而不是只给容器路径：
    # 容器路径常常不止匹配到目标列表（quotes 页面 `div.col-md-8` 就出现 2 次），
    # 只给容器会让抽取器拿到多余元素、甚至一条有效记录都产不出来。
    item_selector = f"{container_css} > {_css_token(items[0])}" if container_css else _css_token(items[0])
    fields.insert(0, {
        "name": "列表容器",
        "selector": item_selector,
        "attribute": "",
        "desc": f"自动检测的重复模式，共 {len(items)} 项",
        "is_container": True,
    })
    return fields


def _infer_leaf_item_fields(items: list[DOMNode]) -> list[dict[str, Any]]:
    """叶子模式：item 本身就是数据（``a.title`` / ``img.thumb`` 之类）。

    此时字段规则用空选择器——抽取器把它解释为"取 item 自身"，
    ``item_selector`` 则精确指到这些 item 上。
    """
    sample = items[:20]
    template = sample[0]
    item_token = _css_token(template)
    container_css = _parent_css(template.css_path)
    item_selector = f"{container_css} > {item_token}" if container_css else item_token

    is_link = all(n.is_link for n in sample)
    is_image = all(n.is_image for n in sample)
    sample_texts = [n.text for n in sample if n.text][:3]

    fields: list[dict[str, Any]] = []
    if is_image:
        name = "图片地址"
        attr = "src"
    elif is_link:
        name = _classify_field(template.tag, " ".join(template.classes), sample_texts) or "链接文本"
        if name in {"链接地址", "图片地址"}:
            name = "链接文本"
        attr = "text"
    else:
        name = _classify_field(template.tag, " ".join(template.classes), sample_texts) or "内容"
        attr = "text"

    fields.append({
        "name": name,
        "selector": "",
        "attribute": attr,
        "desc": f"自动推断: {name}（重复单元自身）",
        "examples": sample_texts,
    })
    if is_link:
        hrefs = [n.href for n in sample if n.href][:3]
        if hrefs:
            fields.append({
                "name": "链接地址",
                "selector": "",
                "attribute": "href",
                "desc": "自动推断: 链接地址（重复单元自身）",
                "examples": hrefs,
            })
    fields.insert(0, {
        "name": "列表容器",
        "selector": item_selector,
        "attribute": "",
        "desc": f"自动检测的重复模式，共 {len(items)} 项",
        "is_container": True,
    })
    return fields


def infer_fields(
    patterns: list[RepeatingPattern],
    nodes: list[DOMNode],
    url: str = "",
) -> list[dict[str, Any]]:
    """从重复模式中推断字段定义。"""
    if not patterns:
        # 尝试全页面推断（单页模式）
        return _infer_single_page_fields(nodes, url)

    # 取最高分模式
    best = patterns[0]
    # 找到该模式下的所有子节点来分析
    parent_css = best.css_path

    # 收集所有属于该模式的直接子元素，并按模式签名只保留真正的重复分组
    children = [n for n in nodes if _parent_css(n.css_path) == parent_css]
    items = _items_by_signature(children, best.child_structure) or children

    if not items:
        return _infer_single_page_fields(nodes, url)

    has_children = any(n.children_tags for n in items[:20])
    if has_children:
        fields = _infer_item_fields(parent_css, items, nodes)
        if fields:
            return fields

    return _infer_leaf_item_fields(items)


def _classify_field(tag: str, classes_str: str, sample_texts: list[str]) -> str:
    """根据标签、类名和示例文本推断字段类型。

    2026-09-12 两处修正：

    1. 直接使用规则自带的字段名（``_FIELD_RULES`` 第三项）。旧实现把规则正则
       再逐个 re-搜索一遍来"猜"名字，规则与名字两处漂移（新增一条规则必须同时
       改两处，否则静默落到 ``内容_<tag>``）。
    2. **判据以"标签 + 类名"为主，文本只在没有类名信号时兜底**。旧实现把示例
       文本混进同一个匹配串，于是"描述"这类长正文里偶然出现的 ``star`` / ``time``
       / ``$`` 会劫持字段名——web-scraping.dev 的商品简介正文里出现了匹配词，
       整个字段就被命名成"评分"。
    """
    structural = f"{tag} {classes_str}".lower()

    for pattern, allowed_tags, field_name in _FIELD_RULES:
        # 先检查标签是否匹配（allowed_tags 用 | 分隔，如 "a|span|div"）
        if allowed_tags and tag.lower() not in allowed_tags.split("|"):
            continue
        if re.search(pattern, structural, re.IGNORECASE):
            return field_name

    # 文本兜底：仅当没有类名信号、且文本足够短（短文本才可能"本身即字段值"）
    if not classes_str.strip():
        short_texts = [t for t in sample_texts if 0 < len(t) <= 60]
        if short_texts:
            blob = f"{tag} {' '.join(short_texts)}".lower()
            for pattern, allowed_tags, field_name in _FIELD_RULES:
                if allowed_tags and tag.lower() not in allowed_tags.split("|"):
                    continue
                if re.search(pattern, blob, re.IGNORECASE):
                    return field_name

    # 根据标签默认推断
    if tag == "a" and any("http" in t for t in sample_texts):
        return "链接地址"
    if tag == "a":
        return "链接文本"
    if tag == "img":
        return "图片地址"
    if tag in ("h1", "h2", "h3", "h4"):
        return "标题"
    if tag == "time":
        return "日期"
    if tag == "li":
        return "列表项"

    return f"内容_{tag}"


def _infer_single_page_fields(nodes: list[DOMNode], url: str = "") -> list[dict[str, Any]]:
    """单页模式 — 从整个页面提取所有可见文本字段。"""
    fields: list[dict[str, Any]] = []
    seen = set()
    for node in nodes:
        if node.depth < 1 or not node.text or len(node.text) < 5:
            continue
        if node.tag in {"div", "span", "p", "h1", "h2", "h3", "a", "li", "td", "th"}:
            name = _classify_field(node.tag, " ".join(node.classes), [node.text[:50]])
            if name not in seen:
                seen.add(name)
                fields.append({
                    "name": f"{name}_{len(seen)}",
                    "selector": node.css_path,
                    "attribute": "href" if node.is_link else "text",
                    "desc": f"自动推断自: {node.text[:30]}",
                    "examples": [node.text[:80]],
                })
    return fields[:20]  # 最多 20 个字段


# ── 分页检测 ──────────────────────────────────────────────────────────

def detect_pagination(html: str, url: str) -> dict[str, Any] | None:
    """检测页面分页模式。"""
    try:
        from lxml import html as lxml_html
    except ImportError:
        return None

    try:
        root = lxml_html.fromstring(html)
    except Exception:
        return None

    # 检测 URL 参数的页码模式
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    pagination_params = {"page", "p", "pg", "pagenum", "page_no", "pn", "offset", "start"}
    for key in pagination_params:
        if key in params:
            return {
                "type": "url_param",
                "param": key,
                "current_value": params[key][0],
                "description": f"URL 参数翻页: ?{key}=N",
            }

    # 检测链接中的翻页模式
    page_links = root.xpath("//a[contains(@href, 'page=') or contains(@href, '&p=') or contains(@href, '?p=')]")
    if page_links:
        href = page_links[0].get("href", "")
        for key in pagination_params:
            m = re.search(rf"[?&]{key}=(\d+)", href)
            if m:
                return {
                    "type": "url_param",
                    "param": key,
                    "example_href": href,
                    "description": f"链接参数翻页: {key}=N",
                }

    # 检测"下一页"按钮
    next_xpaths = [
        "//a[contains(text(), '下一页')]",
        "//a[contains(text(), 'Next')]",
        "//a[contains(@class, 'next')]",
        "//a[contains(@rel, 'next')]",
        "//button[contains(text(), '下一页')]",
        "//span[contains(@class, 'next')]/a",
        "//li[contains(@class, 'next')]/a",
    ]
    for xp in next_xpaths:
        elements = root.xpath(xp)
        if elements:
            href = elements[0].get("href", "")
            return {
                "type": "next_link",
                "xpath": xp,
                "example_href": href,
                "description": f"下一页链接: {xp}",
            }

    return None


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
    if patterns and patterns[0].count >= 3:
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


def _json_item_path(payload: Any) -> tuple[str, Any] | None:
    """定位"记录数组"的 JSONPath 与样本记录。"""
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            return "$[*]", payload[0]
        return None
    if isinstance(payload, dict):
        # 优先找"元素是对象的数组"这一最常见的 API 包装形态
        for key, value in payload.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return f"$.{key}[*]", value[0]
        # 退一步：对象里直接就是一个记录
        return "$", payload
    return None


def _json_configs(payload: Any, url: str, project_name: str) -> dict[str, Any] | None:
    """把 JSON API 响应转成 ``source.kind: rest`` + ``extract.mode: json`` 配置。

    实测背景（2026-09-12 场景测试）：对 ``jsonplaceholder.typicode.com/users``
    这类纯 API 地址，旧实现只走 HTML 分析路径——响应体是 JSON、DOM 里没有列表，
    于是生成 ``fields: {}`` 的空配置并"成功"退出，用户拿到 0 条记录。
    而 JSON 抽取链路本身完备（同一地址手工配 ``mode: json`` 可稳定取到
    10/10 条、字段完整度 1.0）——缺的只是**路由**。
    """
    located = _json_item_path(payload)
    if located is None:
        return None
    item_path, sample = located

    fields: dict[str, Any] = {}
    if isinstance(sample, dict):
        for key, value in list(sample.items())[:12]:
            if isinstance(value, (dict, list)):
                continue  # 嵌套结构交给用户按需展开，自动配置不猜
            raw_key = str(key)
            label = _JSON_FIELD_NAMES.get(raw_key.lower(), raw_key)
            if label in fields:
                label = raw_key
            fields[label] = {"path": raw_key}
    if not fields:
        return None

    return {
        "project": {"name": project_name},
        "source": {"kind": "rest", "seeds": [url]},
        "crawl": {"max_pages": 1, "same_host": True},
        "http": {"user_agent": user_agent("+bot"), "respect_robots": True},
        "extract": {"mode": "json", "item_path": item_path, "fields": fields},
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
        from .extractors import json_field_values, json_path

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

    from ..extraction.html_tools import node_attr, node_text, parse_html, select_nodes

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
            value = node_attr(node, str(rule["attr"])) if rule.get("attr") else node_text(node)
            if value not in (None, ""):
                data[str(name)] = value
                filled[str(name)] += 1
        if data:
            records += 1
    return {"mode": "html", "items": len(items), "records": records, "fields": filled}


def _check_verified(config: dict[str, Any], html: str) -> dict[str, Any]:
    """试跑生成的配置；采不到记录即抛错（不再静默产出不可用配置）。

    门槛不是"至少 1 条"——列表页只采到 1 条通常说明容器/字段选错了
    （例如 10 条里只有 1 条命中），所以有多个列表项时要求至少 2 条。
    """
    report = verify_config(config, html)
    items = int(report.get("items") or 0)
    floor = 1 if items <= 2 else 2
    if report["records"] >= floor:
        return report

    extract = config.get("extract", {})
    empty = ", ".join(f"{k}({v})" for k, v in report["fields"].items()) or "（无字段）"
    raise AutoConfigUnverifiedError(
        f"自动分析未能产出可用配置：在目标页面上试跑只得到 {report['records']} 条记录"
        f"（识别出 {items} 个列表项，至少需要 {floor} 条）。"
        f"\n  item_selector = {extract.get('item_selector', '') or '（未识别出列表容器）'}"
        f"\n  字段填充情况 = {empty}"
        "\n  建议：该页面可能是 JS 动态渲染、需要登录，或列表结构不常规——"
        "可改用 `omnicrawler templates inspect <URL>` 查看站点识别结果，"
        "或 `omnicrawler visual-select` 手动圈选字段。"
    )
    return report


def analyze_to_config(html: str, url: str = "", project_name: str = "auto_task") -> dict[str, Any]:
    """分析页面并直接生成符合 core/config.py 契约的 OmniCrawler 配置。

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
        json_config = _json_configs(payload, url, project_name)
        if json_config is not None:
            _check_verified(json_config, html)
            return json_config

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
        if f.get("regex"):
            rule["regex"] = f["regex"]
        if f.get("examples"):
            rule["examples"] = f["examples"]
        fields_dict[name] = rule

    config: dict[str, Any] = {
        "project": {"name": project_name},
        "source": {"kind": "browser", "seeds": [url]},
        "crawl": {"max_pages": 200},
        "http": {"user_agent": user_agent("+bot"), "respect_robots": True},
        "extract": {"mode": "html", "fields": fields_dict},
        "outputs": {"jsonl": True, "csv": True, "xlsx": True},
        "browser": {"engine": "playwright", "headless": True},
    }
    if item_selector:
        config["extract"]["item_selector"] = item_selector

    # 分页：契约位置 source.pagination，type=page + parameter（page 语义统一）
    # 分页：契约位置 source.pagination，type=page + parameter（page 语义统一）。
    #
    # next_link 型**不写任何配置**：“下一页”就是同站 <a href>，由
    # source.discover 的通用链接发现处理即可。历史实现把它写成
    # ``browser.actions`` 的“点击下一页”，而 actions 对**每个**渲染页都执行 ——
    # 入口页一点即跳走，第一页内容全丢（实测 quotes.toscrape.com/js：最终 URL
    # 变成 /js/page/2/、正文只剩未渲染骨架、0 条记录；分析期自校验不跑 actions，
    # 所以校验通过 10 条 —— 分析与运行结果不一致的根源就在这里）。
    if analysis.pagination and analysis.pagination["type"] == "url_param":
        config["source"]["pagination"] = {
            "type": "page",
            "parameter": analysis.pagination["param"],
        }

    _check_verified(config, html)
    return config


# ── CLI ────────────────────────────────────────────────────────────────

def _build_probe_config(url: str) -> Any:
    """为自动分析构造一份最小可用的 AppConfig（不写盘、只读）。"""
    from ..core.config import AppConfig

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
    from ..core.models import CrawlRequest

    config = _build_probe_config(url)
    request = CrawlRequest(url=url, render=False)

    errors: list[str] = []
    try:
        from ..fetching.http_client import HTTPFetcher

        fetcher = HTTPFetcher(config)
        result = fetcher.fetch(request)
        body = result.body.decode("utf-8", "replace")
        content_type = result.content_type
        if body.strip() and (result.status < 400):
            return body, content_type
        errors.append(f"HTTP 栈返回状态 {result.status} / 内容长度 {len(body)}")
    except Exception as exc:  # noqa: BLE001 —— 逐级降级，错误汇总后统一上报
        errors.append(f"HTTP 栈失败: {type(exc).__name__}: {exc}")

    try:
        from ..fetching.browser_fetcher import BrowserFetcher

        render_request = CrawlRequest(url=url, render=True)
        with BrowserFetcher(config) as browser:
            result = browser.fetch(render_request)
        body = result.body.decode("utf-8", "replace")
        if body.strip():
            return body, result.content_type
        errors.append("浏览器栈返回空内容")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"浏览器栈失败: {type(exc).__name__}: {exc}")

    raise RuntimeError("；".join(errors) or "未知抓取失败")


def _fetch_rendered(url: str) -> str:
    """只用浏览器栈渲染抓取一页，返回 HTML；失败返回空串。

    静态 HTML 里没有可用列表时（SPA / JS 渲染站点：scrapeme.live 首页、
    quotes.toscrape.com/js 等）用它拿到渲染后的 DOM 再分析。生成的配置本就是
    ``source.kind=browser``，因此"分析用渲染结果 + 运行用浏览器"两者一致。
    """
    from ..core.models import CrawlRequest
    from ..fetching.browser_fetcher import BrowserFetcher

    try:
        config = _build_probe_config(url)
        with BrowserFetcher(config) as browser:
            result = browser.fetch(CrawlRequest(url=url, render=True))
        return result.body.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 —— 渲染不可用则由调用方报原始错误
        return ""

def main() -> None:
    import argparse
    import sys

    import yaml
    parser = argparse.ArgumentParser(description="智能爬虫 — 自动分析网页结构并生成配置")
    parser.add_argument("input", help="HTML 文件路径 或 URL")
    parser.add_argument("-o", "--output", help="输出 YAML 配置路径")
    parser.add_argument("--url", help="页面原始 URL（用于分页检测，当 input 为文件时提供）")
    parser.add_argument("--json", action="store_true", help="输出完整分析 JSON 而非 YAML")
    args = parser.parse_args()

    # 获取 HTML：URL 走自有抓取栈；文件直接读
    html: str
    url = args.url or ""
    if args.input.startswith("http://") or args.input.startswith("https://"):
        url = args.input
        try:
            html, content_type = _fetch_html(args.input)
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

        def _consider(candidate_html: str) -> None:
            nonlocal best_config, best_html, best_records
            try:
                candidate = analyze_to_config(candidate_html, url)
            except AutoConfigUnverifiedError:
                return
            report_candidate = verify_config(candidate, candidate_html) or {}
            records = int(report_candidate.get("records") or 0)
            if records > best_records:
                best_config, best_html, best_records = candidate, candidate_html, records

        _consider(html)
        static_analysis = analyze_page(html, url)
        static_top = static_analysis.patterns[0] if static_analysis.patterns else None
        weak = (
            best_config is None
            or static_top is None
            or static_top.count < 3
            or _is_chrome_path(static_top.css_path)
        )
        if weak and url:
            rendered = _fetch_rendered(url)
            if rendered.strip():
                before = best_records
                _consider(rendered)
                if best_records > before:
                    print(
                        "提示: 静态 HTML 列表不完整，已改用浏览器渲染结果生成配置",
                        file=sys.stderr,
                    )

        if best_config is None:
            print("错误: 自动分析未能产出可用配置（静态与浏览器渲染均无可用列表）", file=sys.stderr)
            raise SystemExit(3)
        config, html = best_config, best_html
        output = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
        report = verify_config(config, html)
        print(
            f"已验证: {report['records']} 条记录 / {report['items']} 个列表项"
            f" / 字段 {report['fields']}",
            file=sys.stderr,
        )

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()

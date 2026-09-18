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
from .item_path import (
    ItemPathCandidate,
    is_close_call,
    is_representable,
    rank_item_paths,
    unrepresentable_path_keys,
)

#: `infer_fields` 会在前这么多个重复模式之间择优（见该函数的"多候选择优"说明）。
_FIELD_CANDIDATES = 3

#: 单页模式最多产出多少个**候选**字段。上限存在的意义是挡住"页面很长 ⇒ 候选爆量"，
#: 不是用来丢数据的判据（2026-09-14 修正前，短值是被长度判据静默丢掉的）。
_MAX_SINGLE_PAGE_FIELDS = 20

#: 构成"列表"所需的最少同类元素数。与 `detect_repeating_patterns` 自身"至少 3 个同类元素"
#: 的口径一致：少于 3 个不叫列表，而应报"未识别出列表"。
_MIN_LIST_ITEMS = 3


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


#: 「有可取值字符」＝含数字 / 字母 / 汉字。
#: **长度不是「是不是数据」的判据**：价格 `9`、单位 `元`、规格 `A4` 都很短，
#: 但它们正是要采的值；而「是不是 UI 装饰」由区域判据（`_is_chrome_path`）负责。
_VALUE_CHAR_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def _has_value_char(text: str) -> bool:
    """文本里是否含至少一个可取值字符（数字 / 字母 / 汉字）。"""
    return bool(_VALUE_CHAR_RE.search(text))


def _is_assembled_container(node: DOMNode, nodes: list[DOMNode]) -> bool:
    """节点文本是否只是其**直接子元素**文本的拼接（容器自身没有额外信息）。

    实测依据（详情页）：
    * ``div.price`` 的文本是 ``'9\\n    元'``（两个子 ``span`` 的拼接）——当字段值会交付
      带换行的脏串；
    * ``div.main`` 的文本与唯一子元素 ``h1`` 完全相同——属重复候选。

    两种都由本判据排除，取更精确的子元素。判据用**绝对 CSS 路径**做父子关系
    （同模块 ``_is_chrome_path`` / ``analyze_to_config`` 已有同款前缀判据的先例）。
    """
    if node.child_count <= 0:
        return False
    depth = node.css_path.count(">")
    prefix = node.css_path + " >"
    child_texts = [
        other.text
        for other in nodes
        if other.css_path.startswith(prefix) and other.css_path.count(">") == depth + 1
    ]
    if not child_texts:
        return False
    return _squash_ws(node.text) == _squash_ws("".join(child_texts))


def _squash_ws(text: str) -> str:
    """抹掉全部空白后比较（HTML 里的缩进与换行不是值的一部分）。"""
    return "".join(text.split())


def _is_heading_key(key: str) -> bool:
    """子元素签名键（``h3.country-name`` 或 ``h3``）是否指向标题标签。"""
    return key.split(".", 1)[0].lower() in _HEADING_TAGS


def _node_signature(node: DOMNode) -> list[str]:
    """与 ``detect_repeating_patterns`` 分组时一致的子元素签名。"""
    keys = node.children_keys or node.children_tags
    return list(keys[:5]) if keys else [f"leaf:{node.tag.lower()}"]


def _items_by_signature(children: list[DOMNode], child_structure: list[str]) -> list[DOMNode]:
    """从容器子元素里取出与模式签名一致的重复分组。

    容器下常混有结构不同的兄弟（页脚 / 分页 / 标题栏）。旧实现把全部兄弟当成
    item，``item_selector`` 便退化成"容器 > 第一个子元素"，抽取器随后在单个子元素
    内部找字段 → 全部落空、0 条记录（scrapethissite 国家列表即此形态：容器
    ``div.col-md-4.country`` 下 250 个 ``h3`` 与 250 个 ``div.country-info`` 各成一组，
    旧实现取 items[0]=h3 生成 ``... > h3.country-name`` 作 item 选择器）。
    """
    want = list(child_structure)
    # `["leaf"]` 是旧口径（全部叶子不分标签）的产物：保留"不过滤"的兼容行为。
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
            # 叶子元素按**标签**区分（`leaf:h1` / `leaf:span`），不能一律叫 `leaf`：
            # 否则 h1 + span.price + p.desc 这种"三种不同叶子"会被当成同一组，
            # 凭空造出一个"重复模式"（实测详情页：容器被选成 `body > article > h1`，
            # 只抽得到标题；且因为"有模式"，本该走的单页兜底反而走不到）。
            sig = "|".join(keys[:5]) if keys else f"leaf:{child.tag.lower()}"
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
    # ★ 2026-09-18（走查 R3.4）：价格规则的标签白名单补 `p` —— 实测 books.toscrape 的
    #   价格正是 `<p class="price_color">`，旧白名单不含 `p` ⇒ 整列落到兜底名 `内容_p`。
    (r"(price|价钱|价格|售价|金额|￥|\$)", "span|div|strong|b|p", "价格"),
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


#: 「值写在 class 名里」的取值词表（走查 R3.6）。
#: 例：books.toscrape 的评分是 `<p class="star-rating Three">`，**文本为空**，
#: 值藏在类名里 ⇒ 旧实现整个字段枚举不到。
#: ★ 这是一张**封闭**的表：只认这些「数字词」。表外的一律不当取值
#:   ——"看到类名就猜它是值"会把 `col-md-6`、`btn-primary` 这类排版类名也当数据。
_WORD_NUMBERS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _class_value_token(node: DOMNode) -> str | None:
    """节点是否把**值**写在 class 名里 —— 是则返回那个词，否则 None。

    两个必要条件，缺一不算：

    1. 这个节点**没有别的取值来源**：无文本、不是图片 / 链接、无 ``itemprop``
       —— 否则 `class="two"` 这类排版类名会被当成数据；
    2. 类名里**恰好只有一个**已知数字词 —— 有两个就无法判断哪个才是值（歧义不猜）。
    """
    if node.text or node.is_image or node.is_link or node.itemprop:
        return None
    found = [c for c in node.classes if c.casefold() in _WORD_NUMBERS]
    return found[0] if len(found) == 1 else None


def _class_value_node(node: DOMNode) -> bool:
    """值写在 class 名里的候选节点（走查 R3.6 的枚举判据）。

    ★ 除 `_class_value_token` 外还要一条：**其余类名本身带字段语义**。
      没有这一条，纯排版类名会产出一个**永远是噪声**的列；
      有了它，"`star-rating` + `Three`" 才成立 ——
      `star-rating` 说出了"这是什么"，`Three` 说出了"值是多少"。
    """
    token = _class_value_token(node)
    if token is None:
        return False
    stable = [c for c in node.classes if c.casefold() != token.casefold()]
    return _has_semantic_signal(node.tag, stable)


def _observed_value_map(tokens: list[str] | tuple[str, ...]) -> dict[str, int]:
    """把**观察到**的 class 取值词映射成数字（走查 R3.6）。

    ★ 只写看到的，不写整张词表 —— 没见过的取值在抽取时会**原样保留**（可见、可回查），
      而不是被静默丢弃或猜一个数。用户看到原始值就知道要扩这张表。
    """
    out: dict[str, int] = {}
    for token in tokens:
        number = _WORD_NUMBERS.get(str(token).casefold())
        if number is not None and str(token) not in out:
            out[str(token)] = number
    return out


def _is_value_class_part(part: str, wanted: set[str]) -> bool:
    """CSS 段里的一小段是不是「值词」类名（`.Three` / `[class~="Three"]`）。"""
    if part.startswith("."):
        return part[1:].casefold() in wanted
    if part.startswith("[class~="):
        return part.split("=", 1)[1].strip().strip('"]').casefold() in wanted
    return False


def _strip_value_classes(selector: str, tokens: list[str] | tuple[str, ...]) -> str:
    """从**最终选择器**里去掉「值写在 class 名里」的那一段：``p.star-rating.Three`` → ``p.star-rating``。

    ★ 为什么可以这样后处理：选择器是**对整批 item 统一使用**的。带值词的选择器只匹配到
      "恰好是那个评分"的商品 —— 实测 books.toscrape 首页 20 条里**只有 3 条**取到评分；
      去掉值词后 `p.star-rating` 才匹配每件商品的评分元素。
    ★ 按 **CSS 段精确处理**，不做子串替换 —— `One` 不能把 `OneThing` 也削掉。
    """
    wanted = {str(token).casefold() for token in tokens if token}
    if not wanted:
        return selector
    kept: list[str] = []
    for segment in selector.split(" > "):
        parts = re.split(r"(?=[.\[])", segment)
        tail = [part for part in parts[1:] if not _is_value_class_part(part, wanted)]
        kept.append("".join([parts[0], *tail]))
    return " > ".join(kept)


def _field_key(node: DOMNode) -> str:
    """字段聚合键：优先 itemprop，其次 tag + class。

    无类名元素额外带上父级签名——``<span><a>(about)</a></span>`` 与
    ``<h3><a>标题</a></h3>`` 里的 ``a`` 都是"无类名链接"，只按 ``tag`` 聚合会把
    完全不同的角色并成一组，恒定文案过滤（见 ``_infer_item_fields``）也就跟着失效。
    """
    if node.itemprop:
        return f"itemprop:{node.itemprop.lower()}"
    if node.classes:
        # ★ 走查 R3.6：值写在 class 名里时（`star-rating Three` / `star-rating Four`），
        #   那个值词**不属于字段身份** —— 带着它聚合，每件商品各成一个键，
        #   出现率必然低于阈值，整列在下一步就被丢掉。去掉值词才能归成同一个字段。
        token = _class_value_token(node)
        key_classes = (
            [c for c in node.classes if c.casefold() != token.casefold()] if token else node.classes
        ) or node.classes
        return f"tag:{node.tag}:{'.'.join(key_classes[:2])}"
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
            if n.css_path.startswith(item.css_path + " > ")
            and n.depth > item.depth
            # ★ 走查 R3.6：`(text or is_image)` 会把「值写在 class 名里」的元素**整类排除**
            #   （books.toscrape 的 `<p class="star-rating Three">` 文本为空）⇒ 评分列
            #   从未进入候选。放行的同时必须能取值 —— 见下面 `attribute: class` + `value_map`；
            #   只放行不取值，得到的是一列**永远为空**的列，比不出现更糟。
            and (n.text or n.is_image or _class_value_node(n))
        ]
        for candidate in descendants:
            key = _field_key(candidate)
            slot = by_key.setdefault(
                key,
                {"node": candidate, "texts": [], "items": 0, "attr": None, "class_values": []},
            )
            slot["items"] += 1
            slot["texts"].append(candidate.text[:200])
            token = _class_value_token(candidate)
            if token is not None:
                # 值写在 class 名里：取值用 `class`，再由 `value_map` 把词翻成数字
                if slot["attr"] is None:
                    slot["attr"] = "class"
                slot["class_values"].append(token)
            elif candidate.itemprop:
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
        # ★ 2026-09-18（走查 R3.5）：**恒定 + 无任何语义类名**才算噪声。
        #   实测反例：`<p class="instock availability">In stock</p>` 在样本内取值恒定，
        #   但它是真实业务列（库存状态）——旧判据把它整列删掉，于是该字段
        #   **从未进入候选**（不是命名错，是根本没枚举到）。这一条可能也是
        #   "评分/库存缺失"在多个电商站上复现的原因。
        if slot["attr"] is None and len(texts) >= 3 and len(set(texts)) == 1:
            marker: DOMNode = slot["node"]
            if not _has_semantic_signal(marker.tag, marker.classes):
                continue
        avg_len = sum(len(t) for t in texts) / max(1, len(texts))
        ranked.append((presence * 0.6 + min(1.0, avg_len / 200) * 0.4, key, slot))
    ranked.sort(key=lambda x: x[0], reverse=True)

    fields: list[dict[str, Any]] = []
    used_names: set[str] = set()
    # 同名消歧按**该名字出现的次数**计数（走查 R3.4）：旧实现用 `len(used_names) + 1`，
    # 于是三个同名列会得到 `价格` / `价格_7` / `价格_9` 这种「后缀看不出是第几个」的名字。
    name_seen: dict[str, int] = {}
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
            name = _classify_field(
                parts[1] if len(parts) > 1 else "",
                classes_hint,
                slot["texts"][:3],
                _ancestor_class_context(desc, item_descendants),
            )
            selector = _unique_relative_selector(desc, item_css, item_descendants)
            # `h3 > a` / `h2 > a` 是极常见的"标题即链接"结构：链接本身没有语义
            # 类名，但父级标题标签已经把语义说清楚了。
            if desc.tag == "a" and desc.parent_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                name = "标题"

        if not name or not selector:
            continue
        name = _unique_field_name(name, used_names, name_seen)
        used_names.add(name)

        rule: dict[str, Any] = {
            "name": name,
            "selector": selector,
            "attribute": slot["attr"] or "text",
            "desc": f"自动推断: {name}",
            "examples": slot["texts"][:3],
        }
        # ★ 走查 R3.6：值写在 class 名里 ⇒ 取到 `class` 之后还要**映射**（`Three` → 3）。
        #   放行而不映射，用户拿到的要么是 `star-rating Three` 原文、要么是空列 ——
        #   都不叫"取到了评分"。
        if slot["class_values"]:
            rule["attribute"] = "class"
            rule["value_map"] = _observed_value_map(slot["class_values"])
            rule["examples"] = slot["class_values"][:3]
            # ★ 选择器不能带"值词"，否则只匹配到恰好是那个评分的商品（实测 20 条只取到 3 条）
            rule["selector"] = _strip_value_classes(str(rule["selector"]), slot["class_values"])
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
    #: 走查 R3.6：单元自身把**值写在 class 名里**（`p.star-rating Three`）时，
    #: 下面两条都是假、`sample_texts` 也是空 —— 旧实现于是给它一个 `text` 取值，
    #: 产出一列**永远为空**的字段。
    class_tokens = [token for n in sample if (token := _class_value_token(n)) is not None]
    # 同理，item_selector 也不能带值词，否则只匹配到恰好那一种取值的元素
    item_selector = _strip_value_classes(item_selector, class_tokens)

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
    elif class_tokens:
        token = class_tokens[0]
        stable = [c for c in template.classes if c.casefold() != token.casefold()]
        name = _classify_field(template.tag, " ".join(stable), sample_texts) or "内容"
        attr = "class"
    else:
        name = _classify_field(template.tag, " ".join(template.classes), sample_texts) or "内容"
        attr = "text"

    single_rule: dict[str, Any] = {
        "name": name,
        "selector": "",
        "attribute": attr,
        "desc": f"自动推断: {name}（重复单元自身）",
        "examples": class_tokens[:3] if attr == "class" else sample_texts,
    }
    if attr == "class":
        single_rule["value_map"] = _observed_value_map(class_tokens)
    fields.append(single_rule)
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
    """从重复模式中推断字段定义。

    **多候选择优（2026-09-13 修正）**：不再无条件取 ``patterns[0]``（分数最高者）。
    实测 woocommerce ``/shop/``：商品卡里 ``<a>``（含 ``img`` + ``h2``）与 ``<li>``
    （含 ``a`` + ``span.price``）都能构成重复模式，而 ``detect_repeating_patterns``
    的"直接子元素含标题"加分（+0.15）让 ``<a>`` 压过 ``<li>`` —— 但**价格在 ``<a>`` 之外**，
    于是字段只剩「标题 + 图片地址」，价格永远取不到（真实场景报告已登记）。

    处置：在**前 ``_FIELD_CANDIDATES`` 个**候选之间按「能推断出多少可用字段」择优，
    同分时保留分数更高者（即先出现的那个）。选这个方案而不是调打分口径，是因为
    打分口径被多处用例与实测站点标定过，动它会牵连其它站点；而"字段更全"是
    该场景的**直接判据**。
    """
    if not patterns:
        # 尝试全页面推断（单页模式）
        return _infer_single_page_fields(nodes, url)

    best_fields: list[dict[str, Any]] = []
    best_key: tuple[int, int] | None = None
    for pattern in patterns[:_FIELD_CANDIDATES]:
        fields = _fields_for_pattern(pattern, nodes)
        if not fields:
            continue
        key = (_usable_field_count(fields), pattern.count)
        if best_key is None or key > best_key:
            best_key, best_fields = key, fields
    if best_fields:
        return best_fields

    return _infer_single_page_fields(nodes, url)


def _usable_field_count(fields: list[dict[str, Any]]) -> int:
    """可用字段数——不含「列表容器」这类结构性条目。"""
    return sum(1 for field in fields if not field.get("is_container"))


def _fields_for_pattern(pattern: RepeatingPattern, nodes: list[DOMNode]) -> list[dict[str, Any]]:
    """按**单个**重复模式推断字段（原 ``infer_fields`` 的主体）。

    抽成独立函数是为了让多候选之间可以分别求值再择优。
    """
    parent_css = pattern.css_path

    # 收集所有属于该模式的直接子元素，并按模式签名只保留真正的重复分组
    children = [n for n in nodes if _parent_css(n.css_path) == parent_css]
    items = _items_by_signature(children, pattern.child_structure) or children

    if not items:
        return []

    if any(n.children_tags for n in items[:20]):
        fields = _infer_item_fields(parent_css, items, nodes)
        if fields:
            return fields

    return _infer_leaf_item_fields(items)


def _explain_no_config(
    html: str, *, static_top: Any, rendered: str, signals: tuple[str, ...] = ()
) -> list[str]:
    """给出「为什么没产出配置」的**分类与下一步**（走查 R2.3 / R3.2）。

    旧实现对每一种失败都打印同一句「静态与浏览器渲染均无可用列表」——
    而实测（0.13.0）10 例失败落在四种完全不同的情形上，下一步动作也各不相同：
    响应根本不是 HTML、响应为空、页面有结构但配置过不了自校验、必须交互才出现列表。
    用户拿不到区分依据，只能挨个试。

    判断只使用**此时已掌握的事实**（原始 HTML、最像列表的静态候选、渲染结果、
    交互信号），不新增探测。
    """
    lines: list[str] = []
    if not html.strip():
        lines.append("判断：响应体为空（空页 / 204 / 被拦后返回空内容）。")
        lines.append(
            "下一步：omnicrawler doctor 检查出网；omnicrawler security-report 看是否被策略拦。"
        )
        return lines
    if "<" not in html or ">" not in html:
        lines.append("判断：响应内容不是 HTML（可能是 JSON / 纯文本 / 未解压的响应体）。")
        lines.append(
            "下一步：JSON 接口走 API 发现 —— omnicrawler api-discover；"
            "否则检查 Content-Type 与编码是否被中间层改写。"
        )
        return lines
    if static_top is not None:
        lines.append("判断：页面上**有**候选列表结构，但生成的配置没通过自校验（字段或列表项判定不过关）。")
        lines.append(
            "下一步：omnicrawler field-suggest 推荐稳定选择器；omnicrawler sample 小样本试跑；"
            "结构复杂时用 omnicrawler visual-select 手工选字段。"
        )
        return lines
    # 走到这里说明连"最像列表的结构"都没找到 —— 交给交互信号给出**具体**方向（R3.2）
    detail = _interaction_failure_hint(signals, html=html)
    if detail:
        lines.extend(detail)
        return lines
    if rendered.strip():
        lines.append("判断：静态与浏览器渲染之后，都没有发现重复列表结构。")
        lines.append(
            "下一步：列表很可能要**交互**（点击 / 滚动 / 表单 / 接受 cookie）才会出现 —— "
            "用 omnicrawler record-actions 录制动作后重跑。"
        )
        return lines
    lines.append("判断：静态与浏览器渲染都没有发现重复列表结构，且渲染未取到内容。")
    lines.append(
        "下一步：omnicrawler record-actions 录制交互；若目标需要登录，改用 templates/authenticated 路径。"
    )
    return lines


def _interaction_failure_hint(signals: tuple[str, ...], *, html: str = "") -> list[str]:
    """失败 + 检出交互信号 ⇒ 给出**指名道姓**的原因与下一步（走查 R3.2）。"""
    if not signals:
        return []
    lines: list[str] = []
    if "iframe" in signals:
        lines.append("判断：页面里的内容在 **iframe** 内 —— 列表不在主文档，所以找不到重复结构。")
        # 走查 R5.2：说完"在 iframe 里"还要给出**定位信息**，否则用户无从下手
        locators = _iframe_locators(html)
        if locators:
            lines.append("定位信息：" + "；".join(locators[:3]))
        lines.append(
            "下一步：omnicrawler record-actions 录制（含进入 frame 的操作）；"
            "或在配置里显式声明 frame 定位后再跑 auto-analyze。"
        )
        return lines
    if "cookie_banner" in signals:
        lines.append("判断：页面有 **cookie/consent 提示**，内容要先接受才可见。")
        lines.append(
            "下一步：omnicrawler record-actions 录制\"接受\"这一击，把动作写进 browser.actions 后重跑。"
        )
        return lines
    if "form" in signals:
        lines.append("判断：页面主要通过**表单**（搜索 / 筛选）产出列表。")
        lines.append(
            "下一步：omnicrawler record-actions 录制表单填写与提交；"
            "若接口是 GET 查询参数，也可直接用 omnicrawler api-discover 找接口。"
        )
        return lines
    if "spa_shell" in signals:
        lines.append("判断：这是 **SPA 外壳**（内容全靠 JS 渲染），而渲染后仍未出现列表。")
        lines.append(
            "下一步：确认渲染等待是否足够；必要时 omnicrawler record-actions 补录交互，"
            "或用 omnicrawler api-discover 从浏览器请求里找数据接口。"
        )
        return lines
    if "infinite_scroll" in signals:
        lines.append("判断：页面标记了**滚动加载**，但滚动后仍未形成稳定的重复列表。")
        lines.append(
            "下一步：适当增大 browser.actions 里 scroll_bottom 的 times，或先用 "
            "omnicrawler record-actions 确认滚动位置与等待时机。"
        )
        return lines
    return []


def _ancestor_class_context(desc: DOMNode, pool: list[DOMNode], *, limit: int = 2) -> str:
    """取 desc 的**祖先类名**（最近 *limit* 层），供字段命名参考。

    走查 R3.4：books.toscrape 的价格结构是
    ``<div class="product_price"><p class="price_color">``，
    命名只看元素自身时，``p`` 的 class ``price_color`` 会因「价格」规则的标签白名单
    不含 ``p`` 而失配 ⇒ 字段被命名成兜底名 ``内容_p``。祖先其实已经把语义说清楚了
    —— 与既有的 ``h3 > a`` ⇒ 「标题」是同一条思路。

    ★ 只取**类名**、不取文本：2026-09-12 那次修正明确过，把示例文本混进匹配串会让
    长正文里的偶然词（``star`` / ``$``）劫持字段名；类名不会有这个问题。
    """
    parts = desc.css_path.split(" > ")
    if len(parts) < 2:
        return ""
    names: list[str] = []
    start = max(0, len(parts) - 1 - limit)
    for depth in range(start, len(parts) - 1):
        prefix = " > ".join(parts[: depth + 1])
        node = next((n for n in pool if n.css_path == prefix), None)
        if node is not None and node.classes:
            names.extend(node.classes)
    return " ".join(names)


#: 交互信号检测（走查 R3.2）。
#
# 背景（0.13.0 实测）：交互类场景 11 例里 8 例连配置都产不出来、另 3 例只拿到 2–9 条，
# 而分析阶段**从不产出任何动作配置**、也**不给任何提示** —— 用户看到的是
# 「自动分析未能产出可用配置」或一个条数明显偏少的结果，无从知道差的是"交互"这一步。
#
# 判据只用**结构性 token**（类名 / id / 元素名 / 脚本里的接线调用），不用自由文本 ——
# 后者在长正文里会误报。
_INTERACTION_SIGNALS: tuple[tuple[str, str], ...] = (
    ("iframe", r"<iframe\b"),
    ("form", r"<form\b"),
    ("cookie_banner", r"<(?:div|aside|section)[^>]+(?:id|class)=[\"'][^\"']*(?:cookie|consent|gdpr)"),
    ("spa_shell", r"<div[^>]+id=[\"'](?:app|root|__next|__nuxt)[\"']"),
)

_SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
#: 「内容随滚动追加」的证据必须在**脚本里的接线调用**或**标记里的专用类名**上。
#: ★ 不能拿 "scroll" 这个词直接匹配 —— CSS 的 `overflow: scroll` 到处都是。
#: 实测取证（quotes.toscrape.com/scroll）：该页用 jQuery `$(window).on('scroll', …)`
#: 轮询 `/api/quotes?page=N`，既没有 `IntersectionObserver` 也没有 `load-more` 类名 ——
#: 早期版本因此完全漏检。
_SCROLL_WIRING_RE = re.compile(
    r"intersectionobserver|infinite[-_]?scroll|load[-_]?more|lazy[-_]?load"
    r"|on\(\s*['\"]scroll|addEventListener\(\s*['\"]scroll|onscroll\s*="
    r"|\$\(\s*window\s*\)\s*\.\s*scroll|window\s*\.\s*onscroll",
    re.IGNORECASE,
)
#: 标记里的专用类名/属性（与既有模板 `generic/infinite_scroll.yaml` 的 `html_contains` 同源）
_SCROLL_MARKUP_RE = re.compile(r"(?:infinite[-_]scroll|load[-_]?more|lazy[-_]load)", re.IGNORECASE)

#: 各类信号建议的滚动轮数（仅"内容随滚动追加"这一类需要真正的动作）
_SCROLL_ROUNDS: dict[str, int] = {"infinite_scroll": 12}


def _interaction_signals(html: str) -> tuple[str, ...]:
    """检出页面里的**交互信号**（滚动 / iframe / 表单 / cookie 提示 / SPA 外壳）。"""
    lowered = html.lower()
    found: list[str] = []
    script_bodies = " ".join(match.group(1) for match in _SCRIPT_RE.finditer(html))
    if _SCROLL_WIRING_RE.search(script_bodies) or _SCROLL_MARKUP_RE.search(lowered):
        found.append("infinite_scroll")
    found.extend(
        name for name, pattern in _INTERACTION_SIGNALS if re.search(pattern, lowered)
    )
    return tuple(found)


def _interaction_advice(
    signals: tuple[str, ...], *, scroll_rounds: int, iframe_locators: tuple[str, ...] = ()
) -> list[str]:
    """把交互信号翻译成「我们做了什么 / 你还需要做什么」。

    分两类（这是 R3.2 的核心取舍）：
    - **能安全自动化的**：滚动。它不改服务端状态、不需要选择器、轮数有界 ⇒ 直接产出
      `browser.actions`（`wait_ms` + `scroll_bottom`）。
    - **不能安全自动化的**：点击（cookie 接受、加载更多按钮）、表单提交、进入 iframe。
      自动猜选择器可能点到错误的东西 ⇒ **只给告警**，并给出 `record-actions` 的确切用法。
      ★ 不"顺手"生成动作是刻意的：既有代码注释记录过把 next-link 写成"点击下一页"
      导致入口页第一页内容全丢（实测 quotes.toscrape.com/js 最终 0 条）。
    """
    advice: list[str] = []
    if scroll_rounds:
        advice.append(
            f"检测到内容随滚动追加（infinite-scroll / load-more）⇒ 已自动配置浏览器滚动"
            f"动作（wait_ms + scroll_bottom×{scroll_rounds}），运行期会用浏览器抓取。"
        )
    manual: list[str] = []
    if "cookie_banner" in signals:
        manual.append("cookie/consent 提示（先接受才能看到内容）")
    if "iframe" in signals and "infinite_scroll" not in signals:
        # 走查 R5.2：只说"内容在 iframe 里"没法用 —— 给出**定位信息**才指得到那个 frame
        detail = "；".join(iframe_locators[:3])
        manual.append("内容在 iframe 里" + (f"（定位：{detail}）" if detail else ""))
    if "form" in signals and "infinite_scroll" not in signals:
        manual.append("需要提交表单（搜索 / 筛选）")
    if "spa_shell" in signals and not scroll_rounds:
        manual.append("SPA 外壳，内容靠 JS 渲染")
    if manual:
        advice.append(
            "另外检测到需要人工确认的交互：" + "、".join(manual)
            + "。这类动作**不自动生成**（猜错选择器可能点到别的东西）——"
            "请用 `omnicrawler record-actions <URL>` 录一遍，它会把动作写进 "
            "`browser.actions`，再重跑 `auto-analyze` 或直接补进配置。"
        )
    return advice


def _unique_field_name(name: str, used_names: set[str], name_seen: dict[str, int]) -> str:
    """同名消歧：按**该名字已出现的次数**编号（``价格`` / ``价格_2`` / ``价格_3``）。

    走查 R3.4：旧实现用 ``len(used_names) + 1``，后缀与"第几个同类字段"无关 ——
    实测出现过 ``价格_6``、``标题_2`` 这种看不出关系的编号，用户无法判断
    ``价格_6`` 和 ``价格`` 是不是同一类字段。
    """
    if name not in used_names:
        return name
    name_seen[name] = name_seen.get(name, 1) + 1
    return f"{name}_{name_seen[name]}"


def _has_semantic_signal(tag: str, classes: list[str]) -> bool:
    """元素的**标签 + 类名**是否已足以判定字段语义（即不落到兜底名 ``内容_<tag>``）。

    用途：区分「模板噪声」与「取值恒定的真实业务列」（走查 R3.5）。
    """
    if not classes:
        return False
    return _classify_field(tag, " ".join(classes), []) != f"内容_{tag}"


def _classify_field(
    tag: str, classes_str: str, sample_texts: list[str], ancestor_classes: str = ""
) -> str:
    """根据标签、类名、祖先类名与示例文本推断字段类型。

    2026-09-12 两处修正：

    1. 直接使用规则自带的字段名（``_FIELD_RULES`` 第三项）。旧实现把规则正则
       再逐个 re-搜索一遍来"猜"名字，规则与名字两处漂移（新增一条规则必须同时
       改两处，否则静默落到 ``内容_<tag>``）。
    2. **判据以"标签 + 类名"为主，文本只在没有类名信号时兜底**。旧实现把示例
       文本混进同一个匹配串，于是"描述"这类长正文里偶然出现的 ``star`` / ``time``
       / ``$`` 会劫持字段名——web-scraping.dev 的商品简介正文里出现了匹配词，
       整个字段就被命名成"评分"。

    2026-09-18 追加（走查 R3.4）：``ancestor_classes`` 是**祖先类名**（只取最近两层、
    只取 class），且**只作为自身标签+类名匹配不上时的兜底**，不与自身信号并进同一个串。
    实测教训：把两者拼在一起时，``<div class="product_price">`` 的两个子元素
    （``p.price_color`` 与 ``p.instock.availability``）会**同时**命中「价格」规则 ——
    库存列被命名成价格、还因排序压过了真正的价格列。分两趟匹配后，
    自身信号永远优先，祖先只在确实没有自身信号时才说话。
    仍然**不掺示例文本** —— 上面第 2 条的结论不受影响。
    """
    own = f"{tag} {classes_str}".lower()
    for pattern, allowed_tags, field_name in _FIELD_RULES:
        # 先检查标签是否匹配（allowed_tags 用 | 分隔，如 "a|span|div"）
        if allowed_tags and tag.lower() not in allowed_tags.split("|"):
            continue
        if re.search(pattern, own, re.IGNORECASE):
            return field_name

    # 祖先类名兜底（走查 R3.4）：如 `<div class="product_price"><p class="x">`。
    # 只在自身没给出语义时使用；标签门禁照旧，避免越过元素类型乱命名。
    if ancestor_classes.strip():
        inherited = f"{tag} {ancestor_classes}".lower()
        for pattern, allowed_tags, field_name in _FIELD_RULES:
            if allowed_tags and tag.lower() not in allowed_tags.split("|"):
                continue
            if re.search(pattern, inherited, re.IGNORECASE):
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
    """单页模式 —— 从整个页面提取所有可见文本字段（候选，供人工确认）。

    **2026-09-14 修正（N1c 剩余项，实测驱动）**：旧实现只有一条 ``len(text) < 5`` 判据，
    它同时想干两件事（挡 UI 装饰 + 挡噪声），结果**误伤数据**。实测一个普通商品详情页：
    标题 ``示例商品``（4 字）被丢掉 ⇒ **标题字段整个没有**；``9``（价格）与 ``元``（单位）
    被丢，反倒是父节点 ``div.price`` 的**合并文本** ``'9\\n    元'`` 当了"价格"（脏值）；
    第二个 ``<li>尺码：A4`` 被**按字段名去重**丢掉。现在：

    * 长度规则＝**必须有可取值字符**（数字/字母/汉字），短值不再被丢；
    * UI 装饰改由**区域**判据挡（复用 ``_is_chrome_path``：aside / nav / footer）；
    * 去重键＝``(字段名, CSS 路径)``：同名但**选择器不同**的字段都保留
      （旧实现按名字去重，会把不同元素的字段挤掉）；**同一选择器的重复只出一个**
      ——相同选择器取到的是同一个值，重复列没有意义（同类多个元素属"列表抽取"的范围，
      由重复模式那条路径负责）；
    * 多行容器（文本由子节点拼接）跳过，交给更精确的子节点。

    取舍：本函数产出的是**候选字段**（GUI 会填进表单、CLI 会写进配置），
    宁可多给几个候选，也不静默丢数据；噪声由区域规则挡。上限 ``_MAX_SINGLE_PAGE_FIELDS``。
    """
    fields: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        text = node.text.strip()
        if node.depth < 1 or not text or not _has_value_char(text):
            continue
        if _is_chrome_path(node.css_path) or _is_assembled_container(node, nodes):
            continue
        if node.tag in {"div", "span", "p", "h1", "h2", "h3", "a", "li", "td", "th"}:
            name = _classify_field(node.tag, " ".join(node.classes), [text[:50]])
            key = (name, node.css_path)
            if key not in seen:
                seen.add(key)
                fields.append({
                    "name": f"{name}_{len(seen)}",
                    "selector": node.css_path,
                    "attribute": "href" if node.is_link else "text",
                    "desc": f"自动推断自: {text[:30]}",
                    "examples": [text[:80]],
                })
    return fields[:_MAX_SINGLE_PAGE_FIELDS]


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


#: 分页参数名的候选（与 `detect_pagination` 内部同一张表；这里用于**从 href 里认参数**）。
_PAGINATION_PARAMS: tuple[str, ...] = (
    "page", "p", "pg", "pagenum", "page_no", "pn", "offset", "start",
)


def _page_parameter_in(href: str) -> str:
    """从一条链接里认出页码参数名（``/x?page=2`` ⇒ ``page``）；认不出返回空串。"""
    for name in _PAGINATION_PARAMS:
        if re.search(rf"[?&]{name}=\d", href, re.IGNORECASE):
            return name
    return ""


def _max_page_number(html: str, parameter: str) -> int | None:
    """从页面自身取**最大页码**（`?page=2` … `?page=17`）；取不到返回 ``None``。

    ★ 为什么必须从页面里取：`source.pagination.end` 决定"翻到第几页"，而
      `sources.py` 的默认是 `end = start` —— **缺 `end` 的配置根本不会翻页**，
      运行结果只是"少了几页"，还是"成功"。所以这里宁可返回 `None`
      （调用方就**不写配置**、只给建议），也不猜一个数。
    ★ `rel="last"` 的链接 href 里同样带页码参数，所以一并被下面的扫描覆盖。
    """
    numbers = [
        int(match.group(1))
        for match in re.finditer(rf"[?&]{re.escape(parameter)}=(\d{{1,4}})", html, re.IGNORECASE)
    ]
    positive = [number for number in numbers if number >= 1]
    return max(positive) if positive else None


def _pagination_config(
    html: str, detected: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, list[str]]:
    """把检测到的分页信号翻译成 **`core/pagination.py` 契约里的 `page` 形状**（走查 R5.2）。

    ★ **修正一处"配了等于没配"**：旧实现确实会写 `source.pagination`，但只写
      ``{type: page, parameter: X}`` —— **没有 `end`**。而 `sources.py` 的默认是
      ``end = start`` ⇒ 运行期只发第 1 页；`validate_pagination` 又只对**游标形状**
      要求 `next_path`，对页码形状不要求 `end` ⇒ **校验通过、只抓一页**，
      用户以为"翻页已处理"。这里保证 `end` 一定有值（取自页面自身链接）。

    ★ `detect_pagination` 产出的是 `url_param` / `next_link`，**都不是契约里的形状名**，
      所以必须显式翻译，不能原样写进去。

    ★ `next_link` 型**不生成"点击下一页"动作**（历史教训，保留原注释）：
      "下一页"就是同站 `<a href>`，由 ``source.discover`` 的**通用链接发现**跟进即可
      （`crawl` / `browser` 都参与，受 `crawl.max_depth` / `max_pages` / `same_host` 约束）。
      历史实现把它写成 ``browser.actions`` 的"点击下一页"，而 actions 对**每个**渲染页
      都执行 —— 入口页一点即跳走，第一页内容全丢（实测 quotes.toscrape.com/js：最终 URL
      变成 /js/page/2/、正文只剩未渲染骨架、0 条记录；分析期自校验不跑 actions，
      所以校验通过 10 条 —— 分析与运行结果不一致的根源就在这里）。
      ⇒ 只有当下一页链接**带页码参数**时，才翻译成页码式分页配置。

    Returns:
        ``(pagination 或 None, 面向用户的提醒)``。
    """
    if not detected:
        return None, []

    kind = str(detected.get("type") or "")
    if kind == "url_param":
        parameter = str(detected.get("param") or "")
    elif kind == "next_link":
        parameter = _page_parameter_in(str(detected.get("example_href") or ""))
    else:
        return None, []

    if not parameter:
        target = str(detected.get("xpath") or detected.get("example_href") or "")
        return None, [
            "检测到「下一页」链接，它不是页码参数地址 ⇒ **没有写入分页配置、也没有生成点击动作**"
            "（自动点「下一页」曾导致入口页内容全丢）。这类页面由 `crawl` 的**通用链接发现**跟进"
            "（同站链接，受 `crawl.max_depth` 限制）—— 确认能翻到时无需额外配置。"
            + (f"该链接定位：{target}；" if target else "")
            + "若链接发现翻不到，请用 `omnicrawler record-actions` 录制，"
            "或手工声明 `source.pagination`（页码式填 `parameter` + `end`）。"
        ]

    end = _max_page_number(html, parameter)
    if not end or end < 2:
        return None, [
            f"检测到分页（`{parameter}`），但页面上看不出共有多少页 ⇒ **没有写入** `source.pagination`"
            "（只写 `start` 不写 `end` 等于只抓第一页，是假配置）。"
            f"请手工补 `source.pagination: {{type: page, parameter: {parameter}, start: 1, end: N}}`。"
        ]

    notes = [
        f"检测到分页 ⇒ 已写入 `source.pagination`（页码式：`{parameter}` 第 1–{end} 页）。"
        f"`end={end}` 取自页面自身链接里的最大页码；实际页数更多时请调大它。"
    ]
    return {"type": "page", "parameter": parameter, "start": 1, "end": end, "step": 1}, notes


def _iframe_locators(html: str) -> tuple[str, ...]:
    """列出页面里 iframe 的**定位信息**（走查 R5.2：只说"内容在 iframe 里"不够用）。

    优先 ``#id`` → ``[name]`` → ``[src]`` → ``nth-of-type``，并带上 ``src``
    —— 用户据此才能在配置/录制里指到那个 frame。
    """
    try:
        from lxml import html as lxml_html

        root = lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001 —— 定位信息是"锦上添花"，取不到就不给，不影响主流程
        return ()

    locators: list[str] = []
    for index, node in enumerate(root.xpath("//iframe"), 1):
        node_id = str(node.get("id") or "").strip()
        name = str(node.get("name") or "").strip()
        src = str(node.get("src") or "").strip()
        if node_id:
            where = f"iframe#{node_id}"
        elif name:
            where = f'iframe[name="{name}"]'
        elif src:
            where = f'iframe[src="{src[:120]}"]'
        else:
            where = f"iframe:nth-of-type({index})"
        locators.append(f"{where}（src={src[:120] if src else '（未声明）'}）")
    return tuple(locators)


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
    from .extractors import json_path

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
    is_html_list = str(extract.get("mode", "")) != "json" and bool(item_selector)

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
        if f.get("regex"):
            rule["regex"] = f["regex"]
        # 走查 R3.6：值写在 class 名里时，取到 `class` 还要映射（`Three` → 3）
        if f.get("value_map"):
            rule["value_map"] = f["value_map"]
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
    # 走查 R5.2：分页信号此前**只**用来决定 `source_kind`，检测结果被丢掉 ——
    # 用户拿到 `source.kind: crawl` 却没有任何翻页配置，以为"翻页被处理了"。
    # 现在翻译成 `core/pagination.py` 契约里的形状写进配置；翻译不了时**不写假配置**，只给建议。
    pagination, pagination_notes = _pagination_config(html, analysis.pagination)
    if pagination:
        config["source"]["pagination"] = pagination
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

    # 获取 HTML：URL 走自有抓取栈；文件直接读
    html: str
    url = args.url or ""
    item_path_override = str(getattr(args, "item_path", "") or "")
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
            nonlocal best_config, best_html, best_records, chosen_from_render, chosen_advisories
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
            if records > best_records:
                best_config, best_html, best_records = candidate, candidate_html, records
                chosen_from_render = from_render
                chosen_advisories = local_advisories

        _consider(html, from_render=False)
        static_analysis = analyze_page(html, url)
        static_top = static_analysis.patterns[0] if static_analysis.patterns else None
        weak = (
            best_config is None
            or static_top is None
            or static_top.count < 3
            or _is_chrome_path(static_top.css_path)
        )
        rendered_html = ""
        if weak and url:
            rendered = _fetch_rendered(url)
            if rendered.strip():
                rendered_html = rendered
                before = best_records
                _consider(rendered, from_render=True)
                if best_records > before:
                    print(
                        "提示: 静态 HTML 列表不完整，已改用浏览器渲染结果生成配置",
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
        # 走查 R3.1：让用户知道运行期会用哪种抓取方式（这直接决定耗时可否省下浏览器）。
        chosen_kind = str((config.get("source") or {}).get("kind") or "")
        if chosen_kind in {"static_html", "crawl"} and not chosen_from_render:
            print(
                f"提示: 静态 HTML 已足以生成配置，运行期按 {chosen_kind} 抓取（不启动浏览器）；"
                "如判定失手，加 --always-browser 可强制走浏览器",
                file=sys.stderr,
            )
        # 走查 R3.2：交互信号必须**说出来** —— 要么已自动配好（滚动），要么告知要手工补录。
        for line in _interaction_advice(
            signals, scroll_rounds=scroll_rounds, iframe_locators=_iframe_locators(html)
        ):
            print(f"提示: {line}", file=sys.stderr)
        # 走查 R4.2：记录路径拿不太准（单对象 / 候选接近）、或有嵌套没展开时，如实说出来。
        for line in chosen_advisories:
            print(f"提示: {line}", file=sys.stderr)
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

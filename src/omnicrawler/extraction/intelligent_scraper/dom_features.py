"""DOM 特征提取与多后端解析（selectolax Lexbor 高速路径 + lxml 标准兼容路径）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from omnicrawler.core.capabilities import default_capability_slots

_CSS_IDENT = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")
_SKIP_TAGS = frozenset({"head", "script", "style", "noscript", "meta", "link"})
_STOP_TAGS = frozenset({"head", "script", "style", "noscript"})


@lru_cache(maxsize=8192)
def _parent_css(css_path: str) -> str:
    """去掉 css_path 的最后一段（父级路径）。

    ★ 2026-11-27 加缓存：列表页里**所有行共用同一批路径字符串**（这正是"重复结构"
      的定义），所以同一个 css_path 会被算上百次。实测 scrapethissite/simple
      一次分析里 `_parent_css` 被调 12.7 万次、占 0.26s，几乎全是重复入参。
      它是**纯函数**，缓存不改变任何语义；上限 8192 条足以覆盖单页所有路径，
      长跑进程里也不会无界增长。
    """
    parts = [p.strip() for p in css_path.split(">")]
    return " > ".join(parts[:-1]) if len(parts) > 1 else css_path


@dataclass
class DOMNode:
    """轻量 DOM 节点表示。"""

    tag: str = ""
    classes: list[str] = field(default_factory=list)
    id_: str = ""
    text: str = ""
    href: str = ""
    title: str = ""
    src: str = ""
    depth: int = 0
    child_count: int = 0
    children_tags: list[str] = field(default_factory=list)
    children_keys: list[str] = field(default_factory=list)
    parent_tag: str = ""
    parent_key: str = ""
    itemprop: str = ""
    # ★ 2026-11-26：`data-testid` 是现代站点的**显式语义标注**（Playwright/Cypress 惯例）。
    #   实测（web-scraping.dev/reviews）：整个数据区没有任何 class，只有
    #   `data-testid="review-text"` / `"review-date"` / `"review-stars"`，
    #   旧实现因此把两列都命名成兜底的 `内容_p` / `内容_span`。
    testid: str = ""
    # 2026-11-26: data-label 是另一种显式语义标注（Bootstrap 示例站与很多模板站
    #   用它标「这个值是什么」）。实测 randomuser.me：
    #     <li data-label="name" data-value="...">  <li data-label="email" ...>
    #   语义比 class 清楚，而旧实现完全没读这个属性。
    label: str = ""
    #: aria-label -- 无障碍标注，站点常把它写成可读的名字。
    aria_label: str = ""
    #: `data-value` —— 与 data-label 成对出现的值（实测 randomuser.me）。
    data_value: str = ""
    xpath: str = ""
    css_path: str = ""
    is_link: bool = False
    is_image: bool = False
    is_heading: bool = False
    is_list_item: bool = False


def _parse_dom(html: str, *, max_nodes: int = 5000, parser: str = "auto") -> list[DOMNode]:
    """解析 HTML 为 DOMNode 列表（通过 CapabilitySlot('html_parser') 择优路由）。"""
    if not html or not html.strip():
        return []
    slot = default_capability_slots().slot("html_parser")
    provider = slot.active(parser) if slot is not None else None
    if provider is not None and provider.factory is not None:
        custom = provider.factory(html, max_nodes=max_nodes)
        if isinstance(custom, list):
            return custom

    chosen_id = provider.provider_id if provider is not None else "lxml"
    head_snip = html[:1024].lower()
    is_full_doc = "<html" in head_snip or "<body" in head_snip
    if chosen_id == "selectolax" and is_full_doc:
        parsed = _parse_dom_selectolax(html, max_nodes=max_nodes)
        if parsed is not None:
            return parsed
    return _parse_dom_lxml(html, max_nodes=max_nodes)


def _parse_dom_selectolax(html: str, *, max_nodes: int = 5000) -> list[DOMNode] | None:
    """基于 selectolax Lexbor 引擎的快速 DOM 特征构建路径；不可用时回退 None。"""
    try:
        from selectolax.lexbor import LexborHTMLParser
    except ImportError:
        return None
    try:
        tree = LexborHTMLParser(html)
    except (ValueError, RuntimeError):
        return []
    root = tree.root
    if root is None:
        return []
    nodes: list[DOMNode] = []
    # 2026-11-26: Lexbor 给 table 隐式补 tbody，而抽取引擎（bs4 html.parser）不补。
    #   分析期生成的 table > tbody > tr 于是与真实 run 的 DOM 对不上（实测 25 条 vs 0 条）。
    #   只按源文档是否显式写了 tbody 决定要不要把它写进路径。
    has_tbody = "<tbody" in html.lower()
    _walk_lexbor(root, nodes, depth=0, max_nodes=max_nodes, include_tbody=has_tbody)
    return nodes


def _lexbor_tag(node: Any) -> str:
    raw = getattr(node, "tag", None)
    if not isinstance(raw, str) or raw.startswith("-"):
        return ""
    return raw.lower()


def _lexbor_classes(attrs: dict[str, str | None]) -> list[str]:
    cls_raw = attrs.get("class") or ""
    return [c for c in cls_raw.split() if c]


#: css_path 的最大段数（与 `_build_css_lexbor` 的 `len(parts) > 32` 对应）。
#: 超过时**保留最内层**的这么多段 —— 与"自底向上插入、满了就停"的语义一致。
_CSS_MAX_SEGMENTS = 33


def _css_segment_lexbor(node: Any) -> str:
    """单个节点自己的 css 段（`tag.class`，类名最多两个）。

    ★ 2026-11-27 抽出：原先每算一个节点的 css_path 都要**沿父链重走一遍**，
      于是整页解析是 O(节点数 × 深度)。抽成段之后可以由父路径拼出来（见 `_walk_lexbor`）。
    """
    tag = _lexbor_tag(node)
    attrs = node.attributes
    classes = _lexbor_classes(attrs)
    css = tag
    for cls in classes[:2]:
        css += f".{cls}" if _CSS_IDENT.match(cls) else f'[class~="{cls}"]'
    return css


def _build_css_lexbor(node: Any, *, include_tbody: bool = True) -> str:
    parts: list[str] = []
    cur = node
    while cur is not None:
        tag = _lexbor_tag(cur)
        if tag in {"html", ""}:
            break
        if tag == "tbody" and not include_tbody:
            cur = cur.parent
            continue
        attrs = cur.attributes
        classes = _lexbor_classes(attrs)
        css = tag
        for cls in classes[:2]:
            css += f".{cls}" if _CSS_IDENT.match(cls) else f'[class~="{cls}"]'
        parts.insert(0, css)
        cur = cur.parent
        if len(parts) > 32:
            break
    return " > ".join(parts)


def _walk_lexbor(
    node: Any,
    nodes: list[DOMNode],
    depth: int,
    max_nodes: int,
    *,
    include_tbody: bool = True,
    parent_css: str = "",
) -> None:
    """深度优先构建 DOMNode 列表。

    ``parent_css`` 是父节点的 css_path —— 传进来就不必再沿父链重走一遍。
    ★ 2026-11-27 实测：整页解析原本是 O(节点数 × 深度)，因为每个节点都重算
      自己的完整路径；scrapethissite/simple（200 KB / 300 余节点）在 `_build_css_lexbor`
      上花了约 0.25s，占整次分析的三分之一。现在由父路径 + 自己的段拼出来，口径不变。
    """
    if len(nodes) >= max_nodes:
        return
    tag = _lexbor_tag(node)
    if not tag or tag in _SKIP_TAGS:
        if tag in _STOP_TAGS:
            return
        child = getattr(node, "child", None)
        while child is not None:
            _walk_lexbor(
                child, nodes, depth, max_nodes,
                include_tbody=include_tbody, parent_css=parent_css,
            )
            child = child.next
        return

    attrs = node.attributes
    text = (node.text(deep=True) or "").strip()[:500]
    classes = _lexbor_classes(attrs)
    id_ = str(attrs.get("id") or "")
    href = str(attrs.get("href") or "")
    title = str(attrs.get("title") or "")
    src = str(attrs.get("src") or "")
    itemprop = str(attrs.get("itemprop") or "").strip()
    testid = str(attrs.get("data-testid") or "").strip()
    label = str(attrs.get("data-label") or "").strip()
    aria_label = str(attrs.get("aria-label") or "").strip()
    data_value = str(attrs.get("data-value") or "").strip()

    children: list[Any] = []
    c = node.child
    while c is not None:
        if _lexbor_tag(c):
            children.append(c)
        c = c.next
    child_tags = [_lexbor_tag(ch) for ch in children]
    child_keys: list[str] = []
    for ch in children:
        ch_cls = _lexbor_classes(ch.attributes)
        ch_tag = _lexbor_tag(ch)
        child_keys.append(f"{ch_tag}.{ch_cls[0]}" if ch_cls else ch_tag)

    # ★ 2026-11-27：由父路径拼出（不再沿父链重走）。
    #   `tbody` 的取舍与 `_build_css_lexbor` 完全一致：源文档没写 tbody 时把它从路径里去掉。
    if tag == "html":
        # `html` 不进路径（老实现遇到 html 就 break，路径从 body 起）
        css = ""
    elif tag == "tbody" and not include_tbody:
        css = parent_css
    else:
        segment = _css_segment_lexbor(node)
        if not parent_css:
            css = segment
        else:
            css = f"{parent_css} > {segment}"
            if css.count(" > ") >= _CSS_MAX_SEGMENTS:
                # 超过上限时保留**最内层**（与"自底向上插入、满了就停"等价）
                css = " > ".join(css.split(" > ")[-_CSS_MAX_SEGMENTS:])
    parent = node.parent
    p_tag = _lexbor_tag(parent) if parent is not None else ""
    p_cls = _lexbor_classes(parent.attributes) if parent is not None and p_tag else []
    parent_key = f"{p_tag}.{p_cls[0]}" if p_cls else p_tag

    nodes.append(
        DOMNode(
            tag=tag,
            classes=classes,
            id_=id_,
            text=text,
            href=href,
            title=title,
            src=src,
            depth=depth,
            child_count=len(children),
            children_tags=child_tags,
            children_keys=child_keys,
            parent_tag=p_tag,
            parent_key=parent_key,
            itemprop=itemprop,
            testid=testid,
            label=label,
            aria_label=aria_label,
            data_value=data_value,
            xpath="",
            css_path=css,
            is_link=bool(href and tag == "a"),
            is_image=bool(src or tag == "img"),
            is_heading=tag in {"h1", "h2", "h3", "h4", "h5", "h6"},
            is_list_item=tag in {"li", "option"},
        )
    )
    for ch in children:
        _walk_lexbor(
            ch, nodes, depth + 1, max_nodes,
            include_tbody=include_tbody, parent_css=css,
        )


def _parse_dom_lxml(html: str, *, max_nodes: int = 5000) -> list[DOMNode]:
    try:
        from lxml import html as lxml_html
    except ImportError as exc:
        raise RuntimeError("lxml 是必须的依赖") from exc
    try:
        root = lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001
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
    testid = str(element.get("data-testid", "") or "").strip() if hasattr(element, "get") else ""
    label = str(element.get("data-label", "") or "").strip() if hasattr(element, "get") else ""
    aria_label = str(element.get("aria-label", "") or "").strip() if hasattr(element, "get") else ""
    data_value = str(element.get("data-value", "") or "").strip() if hasattr(element, "get") else ""

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
        href=href, title=element.get("title", ""), src=src, depth=depth,
        child_count=len(children), children_tags=child_tags,
        children_keys=child_keys,
        parent_tag=_tag_name(parent) if parent is not None else "",
        parent_key=parent_key,
        itemprop=itemprop,
        testid=testid,
        label=label,
        aria_label=aria_label,
        data_value=data_value,
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


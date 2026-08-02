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
    parent_tag: str = ""
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


def _walk(element: Any, nodes: list[DOMNode], depth: int, max_nodes: int) -> None:
    if len(nodes) >= max_nodes:
        return
    tag = (element.tag or "").lower() if hasattr(element, "tag") else ""
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

    children = list(element) if hasattr(element, "__iter__") else []
    child_tags = [(c.tag or "").lower() for c in children if hasattr(c, "tag")]

    # 生成 CSS 路径
    css = _build_css(element)

    node = DOMNode(
        tag=tag, classes=classes, id_=id_, text=text,
        href=href, src=src, depth=depth,
        child_count=len(children), children_tags=child_tags,
        parent_tag=(element.getparent().tag or "").lower() if hasattr(element, "getparent") and element.getparent() is not None else "",
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
    parts: list[str] = []
    current = element
    while current is not None and hasattr(current, "tag") and (current.tag or "").lower() not in {"html", ""}:
        tag = (current.tag or "").lower()
        classes = list(current.classes) if hasattr(current, "classes") else []
        css = tag
        if classes:
            css += "." + ".".join(classes[:2])
        parts.insert(0, css)
        current = current.getparent() if hasattr(current, "getparent") else None
        if len(parts) > 5:
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


def detect_repeating_patterns(nodes: list[DOMNode]) -> list[RepeatingPattern]:
    """检测页面中的重复模式（列表项）。"""
    # 按 CSS 父路径分组
    parent_groups: dict[str, list[DOMNode]] = {}
    for node in nodes:
        if node.depth < 2:
            continue
        # 提取父级 CSS 路径
        parent_css = _parent_css(node.css_path)
        if parent_css:
            parent_groups.setdefault(parent_css, []).append(node)

    patterns: list[RepeatingPattern] = []
    for parent_css, children in parent_groups.items():
        if len(children) < 3:  # 至少 3 个同类元素
            continue

        # 按子元素 tag 签名分组
        signature_groups: dict[str, list[DOMNode]] = {}
        for child in children:
            sig = "|".join(child.children_tags[:5]) if child.children_tags else "leaf"
            signature_groups.setdefault(sig, []).append(child)

        for sig, group in signature_groups.items():
            if len(group) < 3:
                continue
            # 计算分数：数量多 + 文本多样 = 高概率列表
            texts = [n.text for n in group if n.text]
            unique_texts = len(set(texts))
            score = min(1.0, (len(group) / 10) * 0.5 + (unique_texts / len(group)) * 0.5)

            patterns.append(RepeatingPattern(
                css_path=parent_css,
                count=len(group),
                sample_texts=texts[:5],
                child_structure=sig.split("|") if sig != "leaf" else [],
                depth=group[0].depth if group else 0,
                score=score,
            ))

    # 按分数降序排列
    patterns.sort(key=lambda p: p.score * p.count, reverse=True)
    return patterns[:10]


def _parent_css(css_path: str) -> str:
    parts = [p.strip() for p in css_path.split(">")]
    return " > ".join(parts[:-1]) if len(parts) > 1 else css_path


# ── 字段推断 ──────────────────────────────────────────────────────────

# 字段名推断规则：(tag, class_pattern, text_pattern) → field_name
_FIELD_RULES: list[tuple[str, str, str]] = [
    # (CSS 类名正则, 标签, 文本正则) → 字段名
    (r"(price|价钱|价格|售价|金额|￥|$)", "span|div|strong|b", r""),
    (r"(title|标题|name|名称|heading)", "a|h[1-6]|span|div", r""),
    (r"(date|time|日期|时间|published|pubdate)", "time|span|div|a", r""),
    (r"(author|作者|发布者|writer)", "span|a|div", r""),
    (r"(desc|description|简介|摘要|描述|content)", "p|div|span", r""),
    (r"(img|image|photo|图片|缩略图|thumb)", "img", r""),
    (r"(tag|category|标签|分类|类型|type)", "span|a|div", r""),
    (r"(rating|star|评分|评价|score)", "span|div", r""),
    (r"(location|address|地址|位置|地区)", "span|div|a", r""),
    (r"(phone|tel|电话|手机|联系)", "span|a|div", r""),
]


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

    # 收集所有属于该模式的直接子元素
    items = [n for n in nodes if _parent_css(n.css_path) == parent_css]

    if not items:
        return _infer_single_page_fields(nodes, url)

    # 对子元素内部进行分析 — 区分容器模式和叶子模式
    first_item_texts: dict[str, list[str]] = {}
    # 先检测是否是叶子模式（items 本身即是数据节点）
    sample_children = [n for n in nodes
                       if n.css_path.startswith(items[0].css_path + " > ") and n.depth == items[0].depth + 1]
    if sample_children:
        # 容器模式：items 是容器，数据在子元素中
        for item in items[:10]:
            item_children = [n for n in nodes
                             if n.css_path.startswith(item.css_path + " > ") and n.depth == item.depth + 1]
            for child in item_children:
                key = f"{child.tag}:{'.'.join(child.classes[:2])}"
                if key not in first_item_texts:
                    first_item_texts[key] = []
                if child.text:
                    first_item_texts[key].append(child.text)
    else:
        # 叶子模式：items 本身就是数据（a.title, span.price 等）
        # 按 tag + class 分组收集
        for item in items[:20]:
            key = f"{item.tag}:{'.'.join(item.classes[:2])}"
            if key not in first_item_texts:
                first_item_texts[key] = []
            if item.text:
                first_item_texts[key].append(item.text)
            if item.href:
                link_key = f"{item.tag}:link"
                if link_key not in first_item_texts:
                    first_item_texts[link_key] = []
                first_item_texts[link_key].append(item.href)

    # 推断字段
    fields: list[dict[str, Any]] = []
    for key, texts in first_item_texts.items():
        tag = key.split(":")[0]
        classes_str = key.split(":")[1] if ":" in key else ""

        field_name = _classify_field(tag, classes_str, texts[:3])
        if not field_name:
            continue

        # 生成选择器
        selector = f"{best.css_path} > * {tag}"
        if classes_str:
            selector += "." + ".".join(classes_str.split(".")[:2])

        field: dict[str, Any] = {
            "name": field_name,
            "selector": selector,
            "attribute": "src" if tag == "img" else ("href" if field_name in ("链接地址", "link") else "text"),
            "desc": f"自动推断: {field_name}",
        }
        if texts:
            field["examples"] = texts[:3]
        fields.append(field)

    # 为列表容器生成 item 选择器
    if fields:
        fields.insert(0, {
            "name": "列表容器",
            "selector": best.css_path,
            "attribute": "",
            "desc": f"自动检测的重复模式，共 {best.count} 项",
            "is_container": True,
        })

    return fields


def _classify_field(tag: str, classes_str: str, sample_texts: list[str]) -> str:
    """根据标签、类名和示例文本推断字段类型。"""
    combined = f"{tag} {classes_str} {' '.join(sample_texts)}".lower()

    for pattern, allowed_tags, _ in _FIELD_RULES:
        # 先检查标签是否匹配（allowed_tags 用 | 分隔，如 "a|span|div"）
        if allowed_tags and tag.lower() not in allowed_tags.split("|"):
            continue
        if re.search(pattern, combined, re.IGNORECASE):
            if re.search(r"price|价钱|价格", pattern, re.IGNORECASE):
                return "价格"
            if re.search(r"title|标题|name|名称", pattern, re.IGNORECASE):
                return "标题"
            if re.search(r"date|time|日期|时间", pattern, re.IGNORECASE):
                return "日期"
            if re.search(r"author|作者", pattern, re.IGNORECASE):
                return "作者"
            if re.search(r"desc|description|简介|描述", pattern, re.IGNORECASE):
                return "描述"
            if re.search(r"img|image|photo|图片", pattern, re.IGNORECASE):
                return "图片地址"
            if re.search(r"tag|category|标签|分类", pattern, re.IGNORECASE):
                return "分类"
            if re.search(r"rating|评分", pattern, re.IGNORECASE):
                return "评分"
            if re.search(r"location|address|地址", pattern, re.IGNORECASE):
                return "地址"
            if re.search(r"phone|tel|电话", pattern, re.IGNORECASE):
                return "电话"

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


def analyze_to_config(html: str, url: str = "", project_name: str = "auto_task") -> dict[str, Any]:
    """分析页面并直接生成 OmniCrawler YAML 配置。"""
    analysis = analyze_page(html, url)

    fields_dict: dict[str, Any] = {}
    for f in analysis.fields:
        name = f["name"]
        if "is_container" in f:
            continue
        fields_dict[name] = {
            "selector": f["selector"],
            "attribute": f.get("attribute", "text"),
            "desc": f.get("desc", ""),
        }
        if f.get("examples"):
            fields_dict[name]["examples"] = f["examples"]

    config: dict[str, Any] = {
        "project": {"name": project_name},
        "source": {"kind": "browser", "seeds": [url] if url else ["https://example.com"]},
        "crawl": {"max_pages": 200},
        "http": {"user_agent": "OmniCrawler/2.1 (+bot)", "respect_robots": True},
        "extract": {"mode": "html", "fields": fields_dict},
        "outputs": {"jsonl": True, "csv": True, "xlsx": True},
        "browser": {"engine": "playwright", "headless": True},
    }

    # 加入分页配置
    if analysis.pagination:
        pag = analysis.pagination
        if pag["type"] == "url_param":
            config["crawl"]["pagination"] = {
                "type": "url_param",
                "param": pag["param"],
            }
        elif pag["type"] == "next_link":
            config["browser"]["actions"] = [
                {"action": "click", "selector": pag.get("xpath", ""),
                 "description": "点击下一页"},
            ]

    return config


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse

    import yaml
    parser = argparse.ArgumentParser(description="智能爬虫 — 自动分析网页结构并生成配置")
    parser.add_argument("input", help="HTML 文件路径 或 URL（需安装 crawl4ai）")
    parser.add_argument("-o", "--output", help="输出 YAML 配置路径")
    parser.add_argument("--url", help="页面原始 URL（用于分页检测，当 input 为文件时提供）")
    parser.add_argument("--json", action="store_true", help="输出完整分析 JSON 而非 YAML")
    args = parser.parse_args()

    # 获取 HTML
    html: str
    url = args.url or ""
    if args.input.startswith("http://") or args.input.startswith("https://"):
        url = args.input
        try:
            from ..sources.crawl4ai_bridge import fetch_js_page
            result = fetch_js_page(args.input)
            html = result.html or result.markdown
            if not html:
                print("错误: 无法获取页面内容")
                return
        except ImportError:
            print("错误: 需要安装 crawl4ai 才能从 URL 抓取。请提供 HTML 文件作为输入。")
            return
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
        config = analyze_to_config(html, url)
        output = yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()

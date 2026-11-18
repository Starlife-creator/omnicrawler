"""重复列表模式检测与嵌套去重聚类。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dom_features import DOMNode, _parent_css

_MIN_LIST_ITEMS = 3

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


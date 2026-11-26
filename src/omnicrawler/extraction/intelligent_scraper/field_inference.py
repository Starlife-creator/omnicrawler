"""列表项与单页字段自动推断、语义分类及交互失败诊断提示。"""

from __future__ import annotations

import re
from typing import Any

from .dom_features import _CSS_IDENT, DOMNode, _parent_css
from .pagination import _iframe_locators
from .pattern_detector import (
    RepeatingPattern,
    _has_value_char,
    _is_assembled_container,
    _is_chrome_path,
    _items_by_signature,
)

_FIELD_CANDIDATES = 3
_MAX_SINGLE_PAGE_FIELDS = 20


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
    (r"(tag|标签)", "span|a|div|li", "标签"),
    (r"(category|分类|类型|type|genre)", "span|a|div|li", "分类"),
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


#: 「库存」写在 **item 自身**类名里的词表（走查：scrapeme.live 实测）。
#: ★ 封闭表：只认这几个语义明确的词。`product` / `status-publish` / `col-md-6`
#:   这类排版与状态类名绝不能当数据 —— 那会产出一列与业务无关的噪声。
_ITEM_CLASS_STOCK: dict[str, str] = {
    "instock": "有货",
    "outofstock": "缺货",
    "out-of-stock": "缺货",
}


def _item_class_value_token(node: DOMNode) -> str | None:
    """item 自身是否把**库存状态**写在类名里；是则返回那个词。

    与 `_class_value_token` 的分工：那个管**子元素**上的数字词（`star-rating Three`），
    这个管 **item 自己**上的库存词。两者都不猜表外的类名。
    """
    for cls in node.classes:
        if cls.casefold() in _ITEM_CLASS_STOCK:
            return cls.casefold()
    return None


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
    # 2026-11-26: data-testid 是显式语义标注，比 class 更可信（它就是给自动化用的）。
    #   实测 web-scraping.dev/reviews：数据区没有任何 class，只有 data-testid，
    #   旧实现把两列都命名成兜底的 内容_p / 内容_span。
    if node.testid:
        return f"testid:{node.testid.lower()}"
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


#: ``data-testid`` 的常见命名 → 业务字段名。
#: ★ 这是**封闭表**：只认语义明确的 testid 词根，认不出就回落到普通分类逻辑，
#:   绝不把 `adBanner-wrapper` / `csrf-token` 这类当成业务字段。
_TESTID_RULES: tuple[tuple[str, str], ...] = (
    (r"(?:^|[-_])(?:review|comment)[-_]?text$", "正文"),
    (r"(?:^|[-_])(?:review|comment)[-_]?date$", "日期"),
    (r"(?:^|[-_])(?:review|comment)[-_]?stars?$", "评分"),
    (r"(?:^|[-_])(?:review|comment)[-_]?rating$", "评分"),
    (r"(?:^|[-_])(?:review|comment)[-_]?author$", "作者"),
    (r"(?:^|[-_])(?:review|comment)[-_]?title$", "标题"),
    (r"(?:^|[-_])(?:product|item|post)[-_]?title$", "标题"),
    (r"(?:^|[-_])(?:product|item|post)[-_]?price$", "价格"),
    (r"(?:^|[-_])(?:product|item|post)[-_]?name$", "名称"),
    (r"(?:^|[-_])(?:product|item|post)[-_]?image$", "图片地址"),
    (r"(?:^|[-_])(?:product|item|post)[-_]?link$", "链接地址"),
    (r"(?:^|[-_])price$", "价格"),
    (r"(?:^|[-_])title$", "标题"),
    (r"(?:^|[-_])rating$", "评分"),
    (r"(?:^|[-_])date$", "日期"),
)


def _testid_field_name(testid: str, sample_texts: list[str]) -> str:
    """从 ``data-testid`` 推断字段名；认不出就退回普通分类，最后才用兜底名。"""
    lowered = testid.strip().lower()
    # `review-text` 这类：**先整串匹配**，再退回普通分类（普通分类看的是 class，
    # 而 testid 元素通常没有 class —— 所以两者的判定互不干扰）。
    for pattern, name in _TESTID_RULES:
        if re.search(pattern, lowered):
            return name
    return _classify_field("", "", sample_texts) or "内容"


def suggest_closest_field(name: str, available: list[str]) -> str:
    """别名/后缀都中不了时，按**字形相似度**再找一个候选；找不到返回空串。

    实测（2026-11-26）：用户说「商品标题」而页面字段就叫「标题」、说「价 格」
    而页面叫「价格」—— 这类**字形上明显同一回事**的差别，靠枚举同义词永远治不完。

    三条防线，缺一条就会乱配：

    1. **必须唯一**：有多个候选同样像就不猜（歧义不猜是项目既有纪律）；
    2. **必须够像**：包含关系，或 `SequenceMatcher` 比率 >= 0.5；
    3. **长度不小于 2**：单字（如「数」「价」）太容易碰巧像。

    ★ 刻意**不**做语义猜测：「卡片标题」不会配到「正文」，
      那需要模型判断，不是字形相似能解决的 —— 配错了比报错更糟。
    """
    import difflib

    target = str(name or "").strip()
    if len(target) < 2 or not available:
        return ""
    candidates: list[tuple[float, str]] = []
    for field in available:
        other = str(field).strip()
        if len(other) < 2:
            continue
        if target in other or other in target:
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, target, other).ratio()
        if score >= 0.5:
            candidates.append((score, other))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    best_score, best = candidates[0]
    if len(candidates) > 1 and candidates[1][0] == best_score:
        return ""   # 两个一样像 ⇒ 不猜
    return best


def _itemprop_field_name(itemprop: str) -> str:
    key = itemprop.lower()
    if key in _ITEMPROP_NAMES:
        return _ITEMPROP_NAMES[key]
    return f"字段_{itemprop}"


def _table_row_columns(items: list) -> list[str]:
    """第一行数据里**直接子 td** 的签名序列（各列的身份与顺序）。

    ★ 不能按 css_path 去数：同一个列表里所有数据行共用**完全相同的**
    css_path（`tr.team`），按前缀数会把 25 行 x 9 列数成 225，
    而且不同行的同类列还会因 class 差异（text-success / text-danger）
    被当成不同的列（第一版实测数成 11 列）。
    行内的真实结构只有 `children_keys` 记得住 —— 那是**逐节点**的。
    """
    if not items:
        return []
    return [k for k in items[0].children_keys if k.split(".", 1)[0] == "td"]


def _table_column_headers(items: list, nodes: list) -> dict[int, str]:
    """HTML 表格：返回 {列序号: 表头文本}，没有可用表头时返回空字典。

    实测（scrapethissite.com/pages/forms）：25 行 x 9 列全是 td，
    唯一能说明「这一列是什么」的是表头那一行的 th：Team Name / Year / Wins ...
    旧实现只按标签+类名推断，九列全叫 内容_td / 内容_td_2 ...（值对、名字没意义）。

    判据刻意保守：
    * 表头必须在**同一张表**内（按 css 前缀判定）；
    * 数量必须与数据列数一致 —— 不一致说明有 colspan/rowspan，宁可不命名；
    * 文本必须短且互不重复，否则那是说明段落不是列名。
    """
    columns = _table_row_columns(items)
    width = len(columns)
    if width < 2 or not items:
        return {}
    item_css = items[0].css_path
    if " > " not in item_css:
        return {}
    base = item_css.rsplit(" > ", 1)[0] + " > "
    headers: list[str] = []
    for n in nodes:
        if n.tag != "th" or not n.css_path.startswith(base):
            continue
        text = n.text.strip()[:20]
        if not text:
            continue
        headers.append(text)
        if len(headers) == width:
            break
    if len(headers) != width or len(set(headers)) != width:
        return {}
    return dict(enumerate(headers))


def _table_column_index(items: list, td_css: str) -> int:
    """某个 td 在它那一行里的**列序号**（0 起）；不是表内 td 时返回 -1。

    各数据行结构相同，所以用第一行的列签名去比：td 的 css 后缀以某列签名开头
    （`td.pct.text-success` 以 `td.pct` 开头 —— 后缀里的第二个 class 是**取值**，
    不属于列身份）。
    """
    columns = _table_row_columns(items)
    if not columns or not items:
        return -1
    prefix = items[0].css_path + " > "
    if not td_css.startswith(prefix):
        return -1
    rel = td_css[len(prefix):]
    for idx, column in enumerate(columns):
        if rel == column or rel.startswith(column + "."):
            return idx
    return -1


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
            # 2026-11-26: data-testid 节点也放行 —— 值可能不是文本（如 N 个 svg 的星级），
            #   只按「有文本」枚举会让整列缺席。放行之后必须能取值，见 count 规则。
            and (n.text or n.is_image or _class_value_node(n) or n.testid)
        ]
        for candidate in descendants:
            key = _field_key(candidate)
            slot = by_key.setdefault(
                key,
                {"node": candidate, "nodes": [], "texts": [], "items": 0, "attr": None, "class_values": []},
            )
            slot["nodes"].append(candidate)
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

    # ★ 2026-11-26：判据从「含任何候选子节点就删」改成**两步**。
    #
    #   旧判据的实测后果（scrapingcourse.com 的 WooCommerce 价格）：
    #
    #       <span class="price">"$69.00"            <- 真值
    #         <span class="product-price">"$69.00"
    #           <bdi>"$69.00"
    #             <span class="currencySymbol">"$" <- 只剩这个活着
    #
    #   四层都是候选，每层都"含候选"⇒ 全被删，**只剩最里层的货币符号**。
    #   于是「价格」列交付出去全是 `$`，金额丢了且不报错。
    #
    #   第 1 步（保留原意）：**多子聚合**要删。quotes 的 `<span>`（"by X (about)"）
    #   含 `small.author` 与 `<a>(about)</a>` 两个候选，它的文本是把子字段拼成一整块 ——
    #   留着等于用垃圾列淹没真字段。判据：候选后代 **≥2**。
    #
    #   第 2 步：剩下的节点里，**只保留最外层**。单链嵌套（价格那四层）外层的
    #   文本最全且不含杂质，内层只剩片段。旧判据在这一步把外层也误删了。
    all_nodes = [slot["node"] for slot in by_key.values()]

    def _candidates_below(node: DOMNode) -> list[DOMNode]:
        return [o for o in all_nodes if o is not node and _is_ancestor(node, o)]

    def _branches(descs: list[DOMNode]) -> bool:
        """候选后代之间出现了**分叉**吗（存在两个互不为祖先的）。

        ★ 判据必须是"分叉"而不是"数量 ≥2" —— 我第一版写的是数量，结果：

            <span class="price">  "$69.00"
              <span class="product-price">  "$69.00"
                <bdi>  "$69.00"
                  <span class="currencySymbol">  "$"

        四层单链也被当成"多子聚合"整条删掉，最后只剩货币符号 + 名字退化成
        `内容_bdi`。数量 ≠ 结构：**单链嵌套是同一个值的不同粒度，分叉才是把
        多个字段拼成一整块**。
        """
        for i, left in enumerate(descs):
            for right in descs[i + 1:]:
                if not (_is_ancestor(left, right) or _is_ancestor(right, left)):
                    return True
        return False

    for key in [k for k, s in by_key.items() if _branches(_candidates_below(s["node"]))]:
        by_key.pop(key, None)

    # 第 2 步：嵌套链里**只留一个代表**，而不是"一律留最外层"。
    #
    #   ★ 这条我第一版写成"留最外层"，当场被既有测试打红：
    #     books.toscrape 的标题是 `<h3><a title="A Light in the Attic">A Light in the ...`
    #     —— 外层 `h3` 的文本被**截断**（带省略号），完整标题在 `a` 的 `title` 属性里。
    #     留最外层等于把「完整标题」换成「截断标题」。
    #
    #   代表节点的选取标准：**文本最长者优先；文本等长时取更深的那个**。
    #     · 价格的单链：`span.price`("$69.00") 比 `span.currencySymbol`("$") 长 ⇒ 取前者；
    #     · 标题的 h3>a：两者文本等长 ⇒ 取更深的 `a`，从而用上它的 `title` 属性；
    #     · 图片的 a>img：两者文本都是空 ⇒ 取更深的 `img`，从而用上它的 `src`。
    remaining = [slot["node"] for slot in by_key.values()]

    def _representative_rank(node: DOMNode) -> tuple[int, int, int]:
        """嵌套链里"谁最该留下"的排序键（越大越该留）。

        三层判据，按优先级：

        1. **文本更长** —— 价格链里 `span.price`("$69.00") 胜过 `span.currencySymbol`("$")；
        2. **带语义类名** —— scrapingcourse 的 `span.price` 与内层 `bdi` 文本**同样长**，
           但只有前者把"这是价格"说清楚了。只按深度取内层会让字段名退化成 `内容_bdi`；
        3. **更深** —— 前两项都相同时取内层，因为更内层才可能带 `title` / `src`
           这类**完整值**属性：books 的 `<h3><a title="完整标题">截断标题</a></h3>`，
           两者文本一样长、都没有类名，取 `a` 才能用上 `title`。
        """
        return (
            len(node.text.strip()),
            1 if _has_semantic_signal(node.tag, node.classes) else 0,
            node.depth,
        )

    dominated: set[str] = set()
    for slot in by_key.values():
        node = slot["node"]
        for other in remaining:
            if other is node:
                continue
            if not (_is_ancestor(node, other) or _is_ancestor(other, node)):
                continue
            if _representative_rank(other) > _representative_rank(node):
                dominated.add(_field_key(node))
                break
    for key in dominated:
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
            # 2026-11-26: data-testid 也算语义信号 —— 实测 review-stars 的文本恒为空，
            #   只按 class 判定会把它当模板噪声整列丢掉（它的"值"在内层 svg 个数里）。
            if not (_has_semantic_signal(marker.tag, marker.classes) or marker.testid):
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
        elif key.startswith("testid:"):
            # 用 data-testid 当**字段名依据**：它是站点自己写给自动化工具的语义标注。
            testid = key.split(":", 1)[1]
            name = _testid_field_name(slot["node"].testid, slot["texts"][:3])
            selector = f'[data-testid="{testid}"]'
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
        # 2026-11-26: HTML 表格 -> 用 th 表头给 td 列命名（否则全叫 内容_td）。
        table_names = _table_column_headers(sample, nodes)
        if table_names and name.startswith("内容_td") and desc.tag == "td":
            idx = _table_column_index(sample, desc.css_path)
            if idx >= 0 and idx < len(table_names):
                name = table_names[idx]
        name = _unique_field_name(name, used_names, name_seen)
        used_names.add(name)

        rule: dict[str, Any] = {
            "name": name,
            "selector": selector,
            "attribute": slot["attr"] or "text",
            "desc": f"自动推断: {name}",
            "examples": slot["texts"][:3],
        }
        if name == "标题" and all(
            node.title and node.title.startswith(node.text.rstrip(".… ")) for node in slot["nodes"]
        ):
            rule["attribute"] = "title"
            rule["examples"] = [node.title for node in slot["nodes"][:3]]
        if name == "标签":
            rule["all"] = True
            rule["selector"] = _css_token(desc)
        # 2026-11-26: data-testid="review-stars" 这类容器，值是内部重复图形元素的个数
        # （web-scraping.dev/reviews 渲染成 N 个 svg，页面上没有"4 星"这种文本）。
        # 只写 attr 取不到任何东西；必须改成"数子元素"。
        if key.startswith("testid:") and re.search(r"(star|rating)", key) and desc.child_count:
            # 计数对象必须是**直接子元素**里重复出现的那个标签。
            #   实测：review-stars 的直接子元素是 5 个 <svg>，每个 svg 里还有一个
            #   <path>；取"第一个叶子后代"会数到 path（5 个 path 恰好也是 5，
            #   但 3 星时是 3 个 svg / 3 个 path —— 语义上数 svg 才对，
            #   而且 path 只在 svg 里存在，换站点就会错）。
            direct = [
                n for n in item_descendants
                if n.parent_key.startswith(desc.css_path.rsplit(" > ", 1)[-1])
                or n.css_path.startswith(desc.css_path + " > ")
            ]
            # 这里不能拿 depth 比：depth 从 0 起算、css 段数从 1 起算，把 desc.depth + 1 当段数用会选到 svg 的子元素 <path>。
            base_segs = desc.css_path.count(" > ")
            direct = [n for n in direct if n.css_path.count(" > ") == base_segs + 1]
            tag = direct[0].tag if direct else "svg"
            rule["count_selector"] = tag
            rule["attribute"] = "count"
            rule["examples"] = []
            rule["desc"] = f"自动推断: {name}（按内部 {tag} 个数计数）"
            rule.pop("attr", None)
        if name == "标题" and desc.is_link:
            fields.append({"name": "链接地址", "selector": selector, "attribute": "href",
                           "desc": "条目稳定详情链接"})
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

    # ★ 2026-11-26：**item 自身把值写在 class 名里**（scrapeme.live 的库存：
    #   `<li class="product ... instock">`，页面上没有任何"In stock"文本）。
    #   这种值不属于任何一个**子元素**，所以上面那圈"遍历后代"永远枚举不到它 ——
    #   必须单独查 item 自己，并用 `selector: ""` + `attr: class` 取值。
    #   判据沿用封闭词表（`_WORD_NUMBERS`）之外的**库存词**，避免把 `col-md-6`
    #   这类排版类名当数据。
    item_class_values = [
        token for item in items if (token := _item_class_value_token(item)) is not None
    ]
    if item_class_values:
        stock_rule: dict[str, Any] = {
            "name": "库存状态",
            "selector": "",
            "attribute": "class",
            "desc": "自动推断: 库存状态（写在 item 的类名里）",
            "examples": item_class_values[:3],
            "value_map": {"instock": "有货", "outofstock": "缺货", "out-of-stock": "缺货"},
        }
        fields.append(stock_rule)

    if not fields:
        return []
    # item 选择器用"容器 + item 自身签名"，而不是只给容器路径：
    # 容器路径常常不止匹配到目标列表（quotes 页面 `div.col-md-8` 就出现 2 次），
    # 只给容器会让抽取器拿到多余元素、甚至一条有效记录都产不出来。
    item_selector = f"{container_css} > {_css_token(items[0])}" if container_css else _css_token(items[0])
    # 同理 item_selector 不能带库存词，否则只匹配到恰好那一种库存状态的商品。
    if item_class_values:
        item_selector = _strip_value_classes(item_selector, item_class_values)
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


def _product_detail_fields(nodes: list[DOMNode]) -> list[dict[str, Any]]:
    blocks = [node for node in nodes if any(
        re.fullmatch(r"product[-_](?:main|detail|details|info)", cls, re.I) for cls in node.classes
    )]
    if len(blocks) != 1:
        return []
    block = blocks[0]
    children = [node for node in nodes if node.css_path.startswith(block.css_path + " > ")]
    titles = [node for node in children if node.tag == "h1" and node.text.strip()]
    prices = [node for node in children if node.itemprop == "price" or any(
        re.search(r"(?:^|[-_])price(?:$|[-_])", cls, re.I) for cls in node.classes
    )]
    if len(titles) != 1 or len(prices) != 1:
        return []
    return [{"name": "列表容器", "selector": block.css_path, "is_container": True, "record_grain": "detail"},
            {"name": "名称", "selector": _css_token(titles[0]), "attribute": "text"},
            {"name": "价格", "selector": _css_token(prices[0]), "attribute": "text"}]


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
    detail_fields = _product_detail_fields(nodes)
    if detail_fields:
        return detail_fields
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
    # 2026-11-26: 登录表单单独一类 —— form 里有 password 输入才算。
    #   只按「有 form」判定会把带搜索框的普通商品页误判成「需要登录」。
    ("login_form", r"<form\b(?:(?!</form>).)*?type=[\"']password"),
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


#: 「分页容器里是**空的**」—— 静态 HTML 里一条分页链接都没有，但页面明确留着
#: 一个 pagination 挂载点。这只有一种解释：页码由 JS 请求后填进去。
#: 实测取证（webscraper.io/test-sites/e-commerce/ajax）：
#:
#:     <div id="static-pagination"> </div>
#:
#: 而真实数据要等 `app.js` 拉回来。旧实现把这个页面判成「静态 HTML 已足够」⇒
#: 运行期不启动浏览器 ⇒ 只拿到首屏 3 条（该站每页 3 条、共 3 页）。
_EMPTY_PAGINATION_RE = re.compile(
    # 引号两种都要认：真实站点用双引号，测试夹具常写单引号 ——
    # 只认一种会变成「我本地是绿的、真跑是错的」（第一版就漏了单引号）。
    # 引号两种都要认（用 \x22 / \x27 写，避开 Python 字符串自身的引号冲突）：
    # 真实站点用双引号，测试夹具常写单引号 —— 只认一种就会「本地绿、真跑错」。
    r"<(?:div|nav|ul|section)[^>]*(?:id|class)=[\x22\x27][^\x22\x27]*pagination[^\x22\x27]*[\x22\x27][^>]*>\s*</(?:div|nav|ul|section)>",
    re.IGNORECASE,
)


def _interaction_signals(html: str) -> tuple[str, ...]:
    """检出页面里的**交互信号**（滚动 / 分页挂载点 / iframe / 表单 / cookie / SPA）。"""
    lowered = html.lower()
    found: list[str] = []
    script_bodies = " ".join(match.group(1) for match in _SCRIPT_RE.finditer(html))
    if _SCROLL_WIRING_RE.search(script_bodies) or _SCROLL_MARKUP_RE.search(lowered):
        found.append("infinite_scroll")
    found.extend(
        name for name, pattern in _INTERACTION_SIGNALS if re.search(pattern, lowered)
    )
    # ★ 2026-11-26：空的分页挂载点 ⇒ 分页要靠 JS 填。放在最后，因为它是对
    #   "没有静态分页链接"的一个**解释**，不是另一类交互。
    if _EMPTY_PAGINATION_RE.search(html):
        found.append("js_pagination")
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
    if "login_form" in signals:
        # 2026-11-26: 登录门槛是常见一类，单独说清（并指出登录态会被保存）。
        manual.append("登录表单（登录后才有内容；录一次即会保存登录态）")
    elif "form" in signals and "infinite_scroll" not in signals:
        manual.append("需要提交表单（搜索 / 筛选）")
    if "spa_shell" in signals and not scroll_rounds:
        manual.append("SPA 外壳，内容靠 JS 渲染")
    if "js_pagination" in signals:
        # ★ 2026-11-26：这一条**不是**"需要人工确认"，我们已经自动换成浏览器抓取了。
        #   所以它进 advice（说明我们做了什么），不进 manual（要用户动手）。
        advice.append(
            "检测到分页由 JS 填充（页面留下空的分页挂载点）⇒ 运行期已改用浏览器抓取，"
            "静态首屏只有部分数据。"
        )
    if manual:
        advice.append(
            "另外检测到需要人工确认的交互：" + "、".join(manual)
            + "。这类动作**不自动生成**（猜错选择器可能点到别的东西）——"
            "请用 `omnicrawler record-actions <URL>` 录一遍，它会把动作写进 "
            "`browser.actions`，再重跑 `auto-analyze` 或直接补进配置。"
        )
        # 2026-11-26: 录制**同时保存登录态**（这一轮补的能力）。
        #   必须说出来，否则用户不知道「我登录过一次就够了」。
        advice.append(
            "  录制时会一并保存登录态（与抓取共用同一个会话目录）——"
            "在录制窗口里登录一次，之后 run 会自动复用，不必每次重登。"
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

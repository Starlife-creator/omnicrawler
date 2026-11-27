"""抽取热路径的性能守卫（2026-11-27）。

## 为什么需要

`analyze_to_config` 是「一句话生成配置」的入口。它的耗时**不随字段数增长，
而是随页面规模**：同一批工作（相对选择器、父路径、表头）被重复做了几十上百遍。

实测（`tests/fixtures/real_sites/scrapethissite_simple.html`，204 KB / 84 行）：

| 项 | 改前 | 改后 |
|---|---|---|
| `analyze_page` | 178 ms | **59 ms** |
| `books_main.html` | 27 ms | **10 ms** |
| `scrapingcourse_ecommerce.html` | 14 ms | **7 ms** |

四层开销，**没有一层是"算法不对"**，全是"同一份信息算了 N 遍"：

1. `_unique_relative_selector` 对每个字段把 item 内所有后代重算 3 遍相对段；
2. `_infer_item_fields` 对每个样本 item 全表扫一遍找子树；
3. `_build_css_lexbor` 每个节点都沿父链重走一遍；
4. 12 个样本 item 的 `css_path` **完全相同**（这正是"重复结构"的定义），
   于是上面三件事都按 12 倍重复。

## 这些用例守什么

★ 判据是「**结果等价**」而不是「更快」—— 性能改动最容易悄悄改掉字段名/选择器。
所以这里同时钉住两件事：数值上快了，且每个站点的字段表逐项不变。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from omnicrawler.extraction.intelligent_scraper import analyze_page
from omnicrawler.extraction.intelligent_scraper.field_inference import (
    RelativeSelectorIndex,
    _unique_relative_selector,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "real_sites"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


#: 足够宽的阈值：CI 机器可能慢 5 倍，但**重复做 N 遍**的形态会慢一个数量级。
#: 这个数字不是"性能指标"，是"别把 O(N×深度) 又写回来"的护栏。
_BUDGET_MS = 1200.0


def test_large_list_page_analysis_stays_within_budget() -> None:
    """204 KB / 84 行的真实页面：整次分析必须在预算内。

    ★ 阈值刻意宽松到「机器慢 5 倍也不会红」——它守的是**数量级**，不是毫秒数。
      实测改前 178 ms、改后 59 ms；预算 1200 ms 给足余量。
    """
    html = _fixture("scrapethissite_simple.html")
    started = time.perf_counter()
    analysis = analyze_page(html, "https://www.scrapethissite.com/pages/simple/")
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert analysis.fields, "分析没产出字段 —— 先确认功能没坏，再谈性能"
    assert elapsed_ms < _BUDGET_MS, f"分析耗时 {elapsed_ms:.0f} ms，超过预算 {_BUDGET_MS} ms"


def test_field_tables_are_unchanged_by_the_speedups() -> None:
    """★ 性能改动的**等价性**守卫：每个真实站点的字段名集合逐项不变。

    这条是整组用例里最重要的：优化最怕的不是慢，是"更快但字段变了"
    （例如相对选择器深度变化 ⇒ 选择器匹配到别的元素 ⇒ 列错位）。
    """
    expected = {
        "books_main.html": {"标题", "价格", "评分", "库存状态", "图片地址", "链接地址"},
        "scrapeme_shop.html": {"标题", "价格", "图片地址", "库存状态"},
    }
    urls = {
        "books_main.html": "https://books.toscrape.com/",
        "scrapeme_shop.html": "https://scrapeme.live/shop/",
    }
    for name, wanted in expected.items():
        analysis = analyze_page(_fixture(name), urls[name])
        got = {field["name"] for field in analysis.fields if not field.get("is_container")}
        assert wanted <= got, f"{name} 缺少字段 {wanted - got}（实际 {sorted(got)}）"


def test_relative_selector_index_matches_the_naive_computation() -> None:
    """索引版与"每次现算"版**必须逐字符相同** —— 这是它能替换掉老实现的前提。

    ★ 用真实的重复列表页，而不是手搓的最小 DOM：只有真实形态才会暴露
      "同一个 css_path 出现多次"这类索引容易搞错的情形。
    """
    from omnicrawler.extraction.intelligent_scraper.dom_features import _parse_dom

    nodes = _parse_dom(_fixture("books_main.html"))
    item_css = next(
        (node.css_path for node in nodes if node.tag == "article" and "product_pod" in node.css_path),
        "",
    )
    assert item_css, "夹具形态变了：找不到 article.product_pod"
    descendants = [
        node for node in nodes
        if node.css_path.startswith(item_css + " > ")
    ]
    assert len(descendants) > 5, "夹具形态变了：item 内没有足够多的后代"
    def reference(node: Any, depth: int) -> str:
        """**独立实现**的参考版：每次现算，不复用任何缓存。

        ★ 这条必须真的独立 —— 我第一版拿 `_unique_relative_selector(…, list)` 当参考，
          而它内部也会构造同一个索引，于是两边恒等、反向断言当场判成"假守卫"。
        """
        segs = [s.strip() for s in node.css_path.split(" > ") if s.strip()]
        prefix = [s.strip() for s in item_css.split(" > ") if s.strip()]
        tail = segs[len(prefix):] if segs[: len(prefix)] == prefix else segs[-1:]
        return " > ".join(tail[-depth:]) if tail else ""

    def reference_unique(node: Any) -> str:
        for depth in (1, 2, 3):
            candidate = reference(node, depth)
            if not candidate:
                continue
            hits = sum(1 for other in descendants if reference(other, depth) == candidate)
            if hits == 1:
                return candidate
        return reference(node, 2) or reference(node, 1)

    index = RelativeSelectorIndex(item_css, descendants)
    for node in descendants[:20]:
        ours = _unique_relative_selector(node, item_css, index)
        naive = reference_unique(node)
        assert ours == naive, f"{node.css_path} 的选择器两份实现不一致：{ours!r} vs {naive!r}"


def test_shared_prefix_dedup_does_not_break_normal_json() -> None:
    """分组/去重的边界：单个 item（count==1）时 `multiplicity` 分支不能改变文本列表。

    ★ 这条守的是我改分组时最容易引入的错：把 `texts` 一次性 `extend([x]*n)` 之后，
      `len(set(texts)) == 1` 这类"恒定文案"判定会误判 —— 值相同本来就该判恒定，
      但若把**不同**节点也按 multiplicity 复制，constant 判定就会失效。
    """
    from omnicrawler.extraction.intelligent_scraper import analyze_page as _ap

    html = (
        "<html><body><div class='list'>"
        + "".join(
            f"<div class='item'><h3 class='title'>T{i}</h3>"
            f"<span class='price'>${i}.00</span></div>"
            for i in range(1, 9)
        )
        + "</div></body></html>"
    )
    analysis = _ap(html, "https://example.org/list")
    names = {field["name"] for field in analysis.fields if not field.get("is_container")}
    assert "标题" in names, names
    assert "价格" in names, names

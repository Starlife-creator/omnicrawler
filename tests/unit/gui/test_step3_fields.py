"""`gui/wizard/step3_fields.py` 的**纯函数**守卫：选择器判定与 XPath 推荐。

## 为什么测这两个函数（此前整个模块 0% 覆盖）

模块有 433 行，三个类是 Qt 对话框/线程 —— 在 `tests/unit` 里难测。
但其中两个是**纯函数**：

* `selector_kind()`：判断用户输入的是 XPath 还是 CSS；
* `suggest_xpath_candidates()`：按示例文本推荐 XPath 候选。

它们决定「可视化选字段」能不能用对，而且**不依赖 Qt**，完全可测。

## 判据

`selector_kind` 的错判后果很具体：XPath 被当成 CSS 会抛
`SelectorSyntaxError`，用户看到的是一个莫名其妙的解析错误。所以判据是
「常见 XPath 形态都必须认出来」。

`suggest_xpath_candidates` 的要点是**排序**：相似度高的必须在前，
否则用户要在一堆候选里翻。

## 边界

不测三个 Qt 类（对话框交互属 GUI job，本文件只管纯逻辑）。
"""
from __future__ import annotations

import pytest

from omnicrawler.gui.wizard.step3_fields import selector_kind, suggest_xpath_candidates

pytest.importorskip("lxml", reason="需要 lxml 解析 HTML")


# ── selector_kind ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "selector",
    [
        "//div[@class='item']",
        "//*[@id='main']",
        "/html/body/div",
        ".//span/text()",
        "(//a)[1]",
        "@href",
        "div[@class='x']",  # 含 [@ 也算 XPath
    ],
)
def test_common_xpath_forms_are_detected(selector: str) -> None:
    """这些是用户最常粘贴的 XPath 形态，一个都不能漏。"""
    assert selector_kind(selector) == "xpath", f"{selector!r} 被误判为 CSS"


@pytest.mark.parametrize(
    "selector",
    [
        "div.item",
        "#main",
        "a[href]",
        "table > tbody > tr",
        ".product .title",
        "h1",
    ],
)
def test_common_css_forms_are_detected(selector: str) -> None:
    """CSS 选择器不能被误判成 XPath（否则会走错解析分支）。"""
    assert selector_kind(selector) == "css", f"{selector!r} 被误判为 XPath"


def test_empty_selector_defaults_to_css() -> None:
    """空输入返回 css（调用方据此走默认路径，不该抛）。"""
    assert selector_kind("") == "css"
    assert selector_kind("   ") == "css"
    assert selector_kind(None) == "css"  # type: ignore[arg-type]


def test_selector_kind_strips_whitespace() -> None:
    """前后空白不影响判定（用户常从浏览器复制带缩进的选择器）。"""
    assert selector_kind("  //div  ") == "xpath"
    assert selector_kind("\n div.item \t") == "css"


# ── suggest_xpath_candidates ─────────────────────────────────────


def _tree(html: str):
    from lxml import html as lxml_html

    return lxml_html.fromstring(html)


def test_candidates_sorted_by_similarity() -> None:
    """**核心**：相似度必须降序，用户先看到最像的。"""
    tree = _tree(
        "<html><body>"
        "<div><span>商品名称：机械键盘</span></div>"
        "<div><span>完全无关的一段文字内容</span></div>"
        "</body></html>"
    )
    results = suggest_xpath_candidates(tree, ["机械键盘"])
    assert results, "一个候选都没给出"
    scores = [score for _text, _xpath, score in results]
    assert scores == sorted(scores, reverse=True), f"候选未按相似度排序：{scores}"


def test_candidates_respect_top_n() -> None:
    """`top_n` 必须真的限制返回条数。"""
    tree = _tree(
        "<html><body>"
        "<p>目标文本</p><p>目标文本</p><p>目标文本</p><p>目标文本</p>"
        "</body></html>"
    )
    assert len(suggest_xpath_candidates(tree, ["目标文本"], top_n=2)) <= 2


def test_low_similarity_is_filtered_out() -> None:
    """相似度低于 0.6 的候选不该出现在结果里（避免噪声）。"""
    tree = _tree("<html><body><p>abcdefghij</p></body></html>")
    results = suggest_xpath_candidates(tree, ["完全不同的一串中文文字"], top_n=5)
    assert all(score > 0.6 for _t, _x, score in results), (
        f"低相似度候选没被过滤：{[round(s, 2) for _t, _x, s in results]}"
    )


def test_empty_text_nodes_are_skipped() -> None:
    """没有文本的节点不该产生候选（否则满屏空字符串）。"""
    tree = _tree("<html><body><div></div><span>   </span><p>有内容</p></body></html>")
    results = suggest_xpath_candidates(tree, ["有内容"], top_n=10)
    for text, _xpath, _score in results:
        assert text.strip(), "候选里出现了空文本"


def test_candidates_carry_usable_xpath() -> None:
    """返回的 xpath 必须是能用的绝对路径（以 / 开头）。"""
    tree = _tree("<html><body><p>目标</p></body></html>")
    results = suggest_xpath_candidates(tree, ["目标"])
    assert results, "没有候选"
    _text, xpath, _score = results[0]
    assert xpath.startswith("/"), f"xpath 不是绝对路径：{xpath!r}"
    # 真的能用它查到节点
    assert tree.xpath(xpath)


def test_text_is_truncated_to_100_chars() -> None:
    """长文本截断到 100 字符 —— 候选列表要能一眼扫过。"""
    long_text = "很长的内容" * 100
    tree = _tree(f"<html><body><p>{long_text}</p></body></html>")
    results = suggest_xpath_candidates(tree, [long_text[:20]], top_n=3)
    assert results, "长文本没产生候选"
    assert len(results[0][0]) <= 100

"""`_html_needle_hit` 的**子串前置过滤**：纯优化，但必须证明两件事。

## 为什么要钉这条

整词匹配要先做 `lowered not in body` 的廉价前置过滤 —— 不是子串就必然不是整词，
所以这一步**不改变任何判定结果**，只是省掉正则全表扫描。

实测（198 KB 页面，29 个标记）：2.6 ms/标记 → 0.0 ms/标记。

## 两条判据（缺一不可）

| 判据 | 为什么 |
|---|---|
| **等价性**：预过滤前后判定完全相同 | 这是纯优化，改语义就是 bug |
| **有效性**：未命中路径显著更快 | 没有这条，过滤被删掉也没人发现 |

★ 注意：这类**纯优化**不能用 `reverse_assert` 验 —— 去掉优化后测试**仍然全绿**，
  因为结果没变。reverse_assert 第一次跑就正确报了「这条守卫没有真正守住」。
  对纯优化，正确做法是「等价性 + 性能」两条，而不是「破坏后必须红」。
"""

from __future__ import annotations

import re
import timeit

import pytest

from omnicrawler.templates.template_catalog import _html_needle_hit

# ── 等价性 ────────────────────────────────────────────────────────────


def _reference(needle: str, body: str) -> bool:
    """**独立实现**的参考判据：直接按词边界语义写，不经过预过滤。

    刻意不复用 `_html_needle_hit` 的任何代码 —— 复用会让等价性断言恒真
    （本项目已有过一次「拿同一个函数当参考」的假守卫）。
    """
    lowered = needle.casefold()
    if lowered.startswith("<"):
        return lowered in body
    pattern = r"(?<![0-9a-z])" + re.escape(lowered) + r"(?![0-9a-z])"
    return re.search(pattern, body) is not None


_CORPUS: tuple[str, ...] = (
    "",
    "product",
    "products",
    "product_pod",
    "product-card",
    '<div class="Product">',
    '{"@type":"Product"}',
    "our products and production line",
    "subproduct",
    "ecommerce-products",
    "<table><tr><td>x</td></tr></table>",
    "no tables here",
    "a" * 1000 + "product" + "b" * 1000,
)


@pytest.mark.parametrize("body", _CORPUS)
@pytest.mark.parametrize("needle", ["Product", "<table"])
def test_prefilter_does_not_change_the_answer(needle: str, body: str) -> None:
    """★ 等价性：与**独立实现**的参考判据逐样本比对。"""
    assert _html_needle_hit(needle, body.casefold()) is _reference(needle, body.casefold()), (
        f"needle={needle!r} body={body[:40]!r}"
    )


# ── 有效性 ────────────────────────────────────────────────────────────


def test_absent_needle_is_much_faster_than_present_one() -> None:
    """★ 未命中路径必须显著更快 —— 否则前置过滤被删掉也没人发现。

    构造方式：同一个大 body，一个含标记、一个不含。
    不含的那个走预过滤短路，含的那个才进正则。
    """
    big = ("lorem ipsum dolor sit amet " * 8000)
    assert "product" not in big
    hit = big + " product "

    misses = timeit.timeit(lambda: _html_needle_hit("Product", big), number=200)
    hits = timeit.timeit(lambda: _html_needle_hit("Product", hit), number=200)
    assert misses < hits / 1.3, (
        f"未命中路径没有更快（前置过滤失效了？）：miss={misses * 1000:.2f}ms "
        f"hit={hits * 1000:.2f}ms"
    )


def test_prefilter_short_circuits_on_a_198kb_page() -> None:
    """真实体量：198 KB 页面上找一个**不存在**的标记，应当接近零成本。"""
    page = ("<div>country</div>" * 10000).casefold()
    elapsed = timeit.timeit(lambda: _html_needle_hit("priceCurrency", page), number=50)
    per_call_ms = elapsed / 50 * 1000
    assert per_call_ms < 1.0, f"198KB 页面上未命中标记耗时 {per_call_ms:.2f} ms（应接近 0）"

"""`analyze_page` 的 `page_type`：**最像列表的那组落在页面框架里 ⇒ 不是列表页**。

## 事故形态（2026-11-27 实测）

`web-scraping.dev/reviews`（数据靠 graphql 异步取）的**静态 HTML 里没有业务列表**，
只有导航菜单与页脚链接在重复：

    patterns[0] = body > nav.navbar...  count=5  score=0.14

旧判据只看 `patterns[0].count >= 3` ⇒ `page_type = "list"`（confidence **0.57**）。

两层后果：

1. 调用方 `site_inspector._structural_page_type` 据此推荐 `generic/list-detail`
   —— 而那个页面根本没有业务列表；
2. `confidence` 0.57 已经在说「我不确定」，但 `page_type` 仍给了确定答案。

## 判据

与 `_check_verified` 里那条「容器落在页面框架 ⇒ 不是业务列表」**同源**，
只是这里用于**分类**而不是拒绝。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.extraction.intelligent_scraper import analyze_page

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "real_sites"


def _nav_only_page() -> str:
    """只有导航与页脚链接重复的页面（真实形态：数据靠 JS 取）。"""
    links = "".join(f'<li class="nav-item"><a href="/p{i}">Item {i}</a></li>' for i in range(8))
    footers = "".join(f'<li><a href="/f{i}">Footer {i}</a></li>' for i in range(6))
    return "".join([
        "<html><body>",
        f'<nav class="navbar"><ul class="navbar-nav">{links}</ul></nav>',
        '<div class="app-mount"></div>',
        f'<footer><ul class="nav">{footers}</ul></footer>',
        "<script>fetchData()</script>",
        "</body></html>",
    ])


def test_nav_only_page_is_not_classified_as_list() -> None:
    """★ 这条就是事故：导航菜单重复 8 次不该让整页被判成列表。"""
    analysis = analyze_page(_nav_only_page(), "https://example.org/reviews")
    assert analysis.page_type != "list", analysis.page_type


def test_nav_only_page_has_low_confidence_anyway() -> None:
    """配套判据：这个页面上 confidence 本来就很低 —— 分类不该给出确定答案。

    这条不是重复断言：它钉住「confidence 与 page_type 不该矛盾」，
    将来若有人调高 confidence 公式却仍让 chrome 组当列表，这里会红。
    """
    analysis = analyze_page(_nav_only_page(), "https://example.org/reviews")
    assert analysis.confidence < 0.7, analysis.confidence


@pytest.mark.parametrize("fixture", ["books_main.html", "quotes_main.html", "scrapeme_shop.html"])
def test_real_list_pages_are_still_lists(fixture: str) -> None:
    """★ 反方向：真实的业务列表页必须仍判 `list`。

    用的是仓库里已存的**真实站点**夹具，不是合成样本。
    """
    path = FIXTURES / fixture
    if not path.is_file():
        pytest.skip(f"缺少真实站点夹具: {fixture}")
    html = path.read_text(encoding="utf-8", errors="replace")
    analysis = analyze_page(html, "https://example.org/")
    assert analysis.page_type == "list", (
        f"{fixture} 的真实列表被判成 {analysis.page_type}：{analysis.patterns[:1]}"
    )


def test_static_reviews_fixture_without_render_is_not_a_list() -> None:
    """真实夹具反证：`reviews` 的**静态** HTML 里只有导航在重复。"""
    path = FIXTURES / "webscrapingdev_reviews.html"
    if not path.is_file():
        pytest.skip("缺少真实站点夹具")
    html = path.read_text(encoding="utf-8", errors="replace")
    analysis = analyze_page(html, "https://web-scraping.dev/reviews")
    assert analysis.page_type != "list", (
        f"静态 reviews 页只有 nav 在重复，不该判 list：{analysis.patterns[:1]}"
    )

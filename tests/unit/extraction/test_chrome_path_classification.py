"""页面框架（chrome）判据：复合类名必须能识别，主内容容器必须不误伤。

## 事故形态（2026-11-27 实测）

``_is_chrome_path`` 原先要求**类名与词表完全相等**（``aside`` / ``nav`` / ``footer``），
而真实站点的装饰容器几乎都用**复合词**：``w3-sidebar``、``main-navigation``、
``woocommerce-breadcrumb``。于是这些全部漏判 ——

    w3schools.com/html/html_tables.asp  →  item_selector 落在左侧教程目录，
                                          产出 42 条「链接文本」（全是侧边栏链接）

## 判据纪律

改成「按 ``-`` / ``_`` 分词后判词」，**不是子串匹配** —— 子串匹配会把
``belowtopnavcontainer``（w3schools 的**主内容容器**）误判成导航，
把整个页面判成"全是框架"。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.intelligent_scraper.pattern_detector import (
    _class_is_chrome,
    _is_chrome_path,
)


@pytest.mark.parametrize(
    "css_path",
    [
        # 原判据已覆盖的三条（回归保护，不能改坏）
        "body > nav > ul",
        "body > aside > div",
        "body > footer > ul",
        # ★ 新增覆盖：真实站点里的复合类名
        "body > div.w3-sidebar.w3-collapse > div",
        "body > div.sidebar > ul",
        "body > div.main-nav > ul",
        "body > div.main-navigation > ul",
        "body > nav.woocommerce-breadcrumb > a",
        "body > ul.navbar-nav > li",
        "body > div.toc > ul",
        "body > div.site-footer > p",
    ],
)
def test_chrome_containers_are_detected(css_path: str) -> None:
    assert _is_chrome_path(css_path) is True, css_path


@pytest.mark.parametrize(
    "css_path",
    [
        # ★ 主内容容器**绝不能**误伤：它们含 nav / content 之类的子串或词
        "body > div.belowtopnavcontainer > div",
        "body > div.content > ul",
        "body > div.main-content > ul",
        "body > div.table-of-content > ul",
        "body > div.product-grid > div",
        "body > div.w3-main.ws-light-grey > div",
        "body > div.container > div.row",
    ],
)
def test_content_containers_are_not_chrome(css_path: str) -> None:
    assert _is_chrome_path(css_path) is False, css_path


def test_substring_matching_would_have_misfired() -> None:
    """★ 这条钉住"为什么用分词而不是子串"。

    ``belowtopnavcontainer`` 含 ``nav`` 子串；若判据写成子串匹配，它会被当导航，
    整个 w3schools 主内容区被判成"页面框架"、所有列表都被拒。
    """
    assert "nav" in "belowtopnavcontainer"
    assert _is_chrome_path("body > div.belowtopnavcontainer > div") is False


def test_underscore_separated_class_is_split() -> None:
    """分词要同时支持 ``-`` 与 ``_``（两种命名法都常见）。"""
    assert _class_is_chrome("site_sidebar") is True
    assert _class_is_chrome("main_content") is False


def test_empty_class_name_is_not_chrome() -> None:
    """空类名不能因为"没有词"被当成装饰，也不能抛异常。"""
    assert _class_is_chrome("") is False
    assert _is_chrome_path("") is False


def test_tag_position_still_wins() -> None:
    """标签名本身是 aside/nav/footer 时直接判装饰，不看类名。"""
    assert _is_chrome_path("body > nav.anything > a") is True

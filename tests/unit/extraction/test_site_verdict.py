"""站点抓取形态的「一句话结论」（2026-11-26）。

## 为什么要有它

用户原来的问题就是「这站到底能不能抓」，而 CLI 只打印
「已验证: N 条记录 / 字段 {...}」或一段错误 —— 用户得自己反推。

## 一个我当场踩到的误判

第一版判据是「有 `<form>` 且没切浏览器 ⇒ 需要登录」。
实测 **books.toscrape.com 首页有个搜索框**，于是每个正常商品页都被判成
「需要登录后才有内容」。判据必须收窄到**登录表单**（form 里有 password 输入）。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.intelligent_scraper import _interaction_signals
from omnicrawler.extraction.intelligent_scraper.result_quality import (
    VERDICT_BROWSER,
    VERDICT_LOGIN,
    VERDICT_NO_LIST,
    VERDICT_STATIC,
    verdict,
)


def test_login_form_is_distinguished_from_a_search_box() -> None:
    """★ 有密码框才算登录表单。

    只判 `form` 会把「带搜索框的商品列表」整类误判成「需要登录」——
    实测 books.toscrape 首页就有搜索框。
    """
    login = (
        '<html><body><form><input type="text" name="u">'
        '<input type="password" name="p"></form></body></html>'
    )
    search = '<html><body><form><input type="text" name="q"></form></body></html>'
    assert "login_form" in _interaction_signals(login)
    assert "login_form" not in _interaction_signals(search)


#: 有意义的字段（没有它，`verdict` 会判「未找到业务列表」）。
_FIELDS = {"标题": {}, "价格": {}}


@pytest.mark.parametrize(
    ("config", "records", "signals", "used_render", "expected"),
    [
        # 纯静态跑通
        ({"source": {"kind": "crawl"}, "extract": {"fields": _FIELDS}}, 20, ("form",), False, VERDICT_STATIC),
        # 渲染后才拿到
        ({"source": {"kind": "browser"}, "extract": {"fields": _FIELDS}}, 10, (), True, VERDICT_BROWSER),
        # 滚动型
        ({"source": {"kind": "browser"}, "extract": {"fields": _FIELDS}}, 100, ("infinite_scroll",), False, VERDICT_BROWSER),
        # 登录门槛
        ({"source": {"kind": "crawl"}, "extract": {"fields": _FIELDS}}, 3, ("login_form",), False, VERDICT_LOGIN),
        # 什么都没有
        (None, 0, (), False, VERDICT_NO_LIST),
        # ★ 有记录但字段全是兜底名 ⇒ 也是「未找到业务列表」
        #   实测 randomuser.me：42 条记录，字段只有 内容_span。
        ({"source": {"kind": "crawl"}, "extract": {"fields": {"内容_span": {}}}}, 42, (), False, VERDICT_NO_LIST),
    ],
)
def test_verdict_labels(
    config, records, signals, used_render, expected
) -> None:
    label, why = verdict(
        config=config, records=records, signals=signals, used_render=used_render
    )
    assert label == expected
    if label != VERDICT_STATIC:
        assert why, f"{label} 必须给出理由，否则用户不知道该做什么"


def test_static_verdict_does_not_scare_the_user() -> None:
    """静态跑通时不该附带任何「你可能要动手」的话 —— 那是噪声。"""
    label, why = verdict(
        config={"source": {"kind": "crawl"}, "extract": {"fields": _FIELDS}},
        records=20,
        signals=(),
        used_render=False,
    )
    assert label == VERDICT_STATIC
    assert why == ""

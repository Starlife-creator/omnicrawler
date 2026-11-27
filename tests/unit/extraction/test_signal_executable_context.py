"""信号判据必须落在**可执行上下文**里，散文里提到不算。

## 事故形态（2026-11-27 实测，两个一起）

| 页面 | 正文写的 | 旧判定 | 后果 |
|---|---|---|---|
| 讲解 IntersectionObserver 的文档 | 「Use IntersectionObserver to detect visibility」| `pagination=(infinite-scroll,)` | 首推 `generic/infinite-scroll`（「已配置滚动动作」）|
| 讲解 OAuth 的文档 | 「OAuth 2.0 is a delegation protocol」| `auth=(federated-login,)` | 用户以为这站要联邦登录 |

两者与之前那几个缺陷**同一模式**：拿「某个名字在文本里出现过」当「页面在用它」。

## 判据：可执行上下文

标记必须出现在下列之一：

* `<script>` 内容（`new IntersectionObserver(...)`）；
* `class` / `id` / `data-*` 取值（`class="load-more"`）；
* 跳转/提交目标 `href` / `action` / `src`（`href="/oauth/authorize"`）；
* meta 的 `content` / `name`、`rel`（`<meta name="openid.server">`）。

★ 属性清单**必须覆盖 href/action/name**：只查 `class`/`id` 会让
  「真的用 OAuth 登录」的页面漏判 —— 我第一版就是这样，矩阵测试立刻抓出来。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _inspect(body: str):
    request = CrawlRequest("https://example.org/x")
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body.encode(), 0.01
    )
    return inspect_result(result, bundled_template_catalog())


# ── infinite-scroll ────────────────────────────────────────────────────


def test_docs_mentioning_intersection_observer_is_not_infinite_scroll() -> None:
    """★ 散文里提到一个 API 名字，不构成"页面在用它"。"""
    body = (
        "<html><body><article><p>"
        "Use IntersectionObserver to detect visibility."
        "</p></article></body></html>"
    )
    assert "infinite-scroll" not in _inspect(body).pagination


@pytest.mark.parametrize(
    "body",
    [
        # 真用法：脚本里 new 一个 observer
        '<html><body><div id="l"></div>'
        '<script>new IntersectionObserver(cb).observe(el)</script></body></html>',
        # 真用法：class 钩子
        '<html><body><div class="infinite-scroll-container"></div></body></html>',
        # 真用法：load-more 按钮
        '<html><body><button class="load-more">More</button></body></html>',
    ],
)
def test_real_infinite_scroll_is_still_detected(body: str) -> None:
    """★ 反方向：收紧判据不能把真的无限滚动页判掉。"""
    assert "infinite-scroll" in _inspect(body).pagination


# ── federated-login ────────────────────────────────────────────────────


def test_docs_mentioning_oauth_is_not_a_login_requirement() -> None:
    body = "<html><body><article><p>OAuth 2.0 is a delegation protocol.</p></article></body></html>"
    assert "federated-login" not in _inspect(body).authentication


@pytest.mark.parametrize(
    "body",
    [
        # 真用法：跳转目标里带 /oauth/
        '<html><body><a href="/oauth/authorize?client_id=x">Sign in</a></body></html>',
        # 真用法：SAML SSO 链接
        '<html><body><a href="/saml/login">SSO</a></body></html>',
        # 真用法：OpenID meta 声明
        '<html><head><meta name="openid.server" content="x"></head><body>hi</body></html>',
    ],
)
def test_real_federated_login_is_still_detected(body: str) -> None:
    """★ 属性清单必须覆盖 href / action / name —— 只查 class/id 会漏判真登录页。

    这条是我第一版实现的「反面教材」：当时只匹配 `class`/`id`/`data-*`，
    于是「Sign in with GitHub」的 `href="/oauth/authorize"` 完全没被认出来。
    """
    assert "federated-login" in _inspect(body).authentication


def test_plain_list_page_has_neither_signal() -> None:
    page = _inspect("<html><body><ul><li>a</li><li>b</li></ul></body></html>")
    assert "infinite-scroll" not in page.pagination
    assert "federated-login" not in page.authentication

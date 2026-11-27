"""CMS / 框架判据：散文里提到一个名字不算"页面在用它"。

## 事故形态（2026-11-27 实测）

| 页面 | 正文写的 | 旧判定 | 后果 |
|---|---|---|---|
| 讲解 React 的文档 | 「data-reactroot was used in v15」| `frameworks=(react/next,)` | `dynamic=True` ⇒ 首推 `generic/infinite-scroll` |
| 讲解 Discuz 的文档 | 「Legacy apps used forum.php」| `cms=(discuz,)` | 用户以为这站是 Discuz 论坛 |

与 `IntersectionObserver` / `OAuth` / `application/json` / `Product` 同一模式：
**拿「某个名字在文本里出现过」当「页面在用它」**。

## 判据：去掉文本节点

`_executable_text()` 只保留**标签**（含属性名与取值）与 `<script>`/`<style>` 内容，
把 `>` 与 `<` 之间的散文全部丢掉。

这样：

* `<div data-reactroot="">` ⇒ 命中（属性名保留）；
* `<script>window.__NEXT_DATA__=...</script>` ⇒ 命中（脚本内容保留）；
* `<p>data-reactroot was used in v15</p>` ⇒ **不命中**（散文被去掉）。
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import _executable_text, inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _inspect(body: str):
    request = CrawlRequest("https://example.org/x")
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body.encode(), 0.01
    )
    return inspect_result(result, bundled_template_catalog())


# ── _executable_text 本身 ──────────────────────────────────────────────


def test_prose_between_tags_is_dropped() -> None:
    text = _executable_text("<html><body><p>data-reactroot is great</p></body></html>")
    assert "data-reactroot" not in text


def test_attribute_names_are_kept() -> None:
    """属性**名**本身是信号（`data-reactroot` 没有取值）。"""
    assert "data-reactroot" in _executable_text('<div data-reactroot=""><p>x</p></div>')


def test_attribute_values_are_kept() -> None:
    assert "load-more" in _executable_text('<button class="load-more">More</button>')


def test_script_contents_are_kept() -> None:
    text = _executable_text("<script>window.__NEXT_DATA__ = {}</script>")
    assert "__next_data__" in text


# ── 端到端 ─────────────────────────────────────────────────────────────


def test_docs_mentioning_react_is_not_detected_as_react_app() -> None:
    """★ 这条就是事故。"""
    body = "<html><body><article><p>React is a UI library. data-reactroot was used in v15.</p></article></body></html>"
    report = _inspect(body)
    assert report.frameworks == (), report.frameworks
    assert report.dynamic is False
    assert "generic/infinite-scroll" not in [r["id"] for r in report.recommendations]


def test_docs_mentioning_forum_php_is_not_detected_as_discuz() -> None:
    body = "<html><body><article><p>Legacy apps used forum.php endpoints.</p></article></body></html>"
    assert _inspect(body).cms == ()


def test_real_react_app_is_still_detected() -> None:
    """★ 反方向：真的有 `data-reactroot` 属性的页面必须仍判 React。"""
    body = '<html><body><div data-reactroot=""><p>hi</p></div></body></html>'
    assert "react/next" in _inspect(body).frameworks


def test_real_next_data_payload_is_still_detected() -> None:
    body = (
        "<html><body><div id=\"__next\"></div>"
        "<script>window.__NEXT_DATA__={\"props\":{}}</script></body></html>"
    )
    assert "react/next" in _inspect(body).frameworks


def test_real_wordpress_site_is_still_detected() -> None:
    """真站样本：`/wp-content/` 出现在标签属性里（`href`/`src`）。"""
    body = '<html><body><link rel="stylesheet" href="/wp-content/themes/x/style.css"><p>hi</p></body></html>'
    assert "wordpress" in _inspect(body).cms


def test_discuz_in_a_real_form_action_is_detected() -> None:
    body = '<html><body><form action="/forum.php?mod=post"><input name="x"></form></body></html>'
    assert "discuz" in _inspect(body).cms

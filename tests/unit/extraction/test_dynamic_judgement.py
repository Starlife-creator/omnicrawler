"""`dynamic` 判定：**脚本多 ≠ 页面是动态渲染的**。

## 事故形态（2026-11-27 实测）

一个 **JS 教程页**有 15 个代码示例，每个包一个 `<script>`：

    <pre><script>console.log(0)</script></pre>
    <pre><script>console.log(1)</script></pre>
    ...

旧判据 `script_count >= 12` 命中 ⇒ `dynamic=True` ⇒ 推荐
`generic/infinite-scroll`（「已配置滚动动作」，而页面根本没有滚动加载）。

## 判据

真正的「动态渲染」要么有**框架标记**（React/Vue/Angular…），要么有**空挂载点**
（`<div id="root"></div>`）。脚本数量只能作为弱旁证，而且必须与空挂载点**同时**成立。

| 形态 | dynamic |
|---|---|
| 15 个代码示例 `<script>` | **False** |
| `<div id="root">` 空挂载点 + 15 个 script | True |
| `data-reactroot` 框架标记 | True |
| 普通页面 | False |
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _inspect(body: str):
    request = CrawlRequest("https://example.org/x")
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body.encode(), 0.01
    )
    return inspect_result(result, bundled_template_catalog())


def _code_samples(count: int) -> str:
    return "".join(f"<pre><script>console.log({i})</script></pre>" for i in range(count))


def test_docs_page_with_many_code_samples_is_not_dynamic() -> None:
    """★ 这条就是事故：代码示例里的 <script> 不构成动态渲染。"""
    body = f"<html><body><article><h1>Tutorial</h1>{_code_samples(15)}</article></body></html>"
    assert _inspect(body).dynamic is False


def test_spa_shell_with_empty_mount_point_is_dynamic() -> None:
    """★ 反方向：真的 SPA 外壳（空 #root + 一堆 script）必须仍判 dynamic。"""
    scripts = "".join(f'<script src="/s{i}.js"></script>' for i in range(15))
    body = f'<html><body><div id="root"></div>{scripts}</body></html>'
    assert _inspect(body).dynamic is True


def test_framework_marker_is_dynamic() -> None:
    assert _inspect('<html><body><div data-reactroot=""><p>hi</p></div></body></html>').dynamic is True


def test_plain_page_is_not_dynamic() -> None:
    assert _inspect("<html><body><p>hello</p></body></html>").dynamic is False


def test_docs_page_does_not_recommend_infinite_scroll() -> None:
    """★ 端到端：教程页的第一推荐不该是 `generic/infinite-scroll`。"""
    body = f"<html><body><article><h1>Tutorial</h1>{_code_samples(15)}</article></body></html>"
    report = _inspect(body)
    ids = [item["id"] for item in report.recommendations]
    assert "generic/infinite-scroll" not in ids, ids

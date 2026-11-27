"""`auto-analyze` 不重复验证同一份 HTML（2026-11-27）。

## 为什么

`auto-analyze` 的流程是：对每份候选 HTML 调 `analyze_to_config`（内部**已经**试跑验证），
然后为了质量排名**再**调一次 `verify_config`。两次输入完全相同 —— 同一份配置、同一份
HTML，结果必然相同，但每次都要 `parse_html` + 上千次 `select_nodes`。

实测（scrapethissite/simple，204 KB / 84 行）：重复那一次占 **0.4 s**，
是整条 `auto-analyze` 的约三分之二。

## 判据纪律

★ 复用条件必须**两边都比**：`id(config)` 相同 **且** html 哈希相同。
只按 `id` 查是错的 —— 对象被回收后 id 会被下一个 dict 复用，那会张冠李戴地
把另一份 HTML 的报告返回来（静默错误，比慢更坏）。
"""

from __future__ import annotations

import time

from omnicrawler.extraction.intelligent_scraper import (
    analyze_to_config,
    remember_report,
    verified_report,
    verify_config,
)

SPA_HTML = (
    '<html><head><script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"products":['
    '{"id":1,"name":"Alpha","price":9.9},'
    '{"id":2,"name":"Beta","price":19.9},'
    '{"id":3,"name":"Gamma","price":29.9}'
    ']}}}'
    '</script></head><body><div id="__next"></div></body></html>'
)


def test_analyze_to_config_leaves_a_reusable_report() -> None:
    """★ 这是复用的前提：`analyze_to_config` 必须真的把报告留下来。"""
    config = analyze_to_config(SPA_HTML, "https://example.org/shop")
    report = verified_report(config, SPA_HTML)
    assert report is not None, "analyze_to_config 没有留下验证报告"
    assert report["records"] == 3
    # 与现算的结果一致（否则复用会把结论带偏）
    assert verify_config(config, SPA_HTML) == report


def test_report_is_not_reused_for_a_different_html() -> None:
    """★ 反方向：换了 HTML 就必须重算 —— 否则会把旧页面的结论当成新页面的。"""
    config = analyze_to_config(SPA_HTML, "https://example.org/shop")
    other = SPA_HTML.replace("Alpha", "Zulu")
    assert verified_report(config, other) is None, "换了 HTML 却复用了旧报告"


def test_report_is_not_reused_for_a_new_config_object() -> None:
    """id 不同（另一份配置）当然不能复用。"""
    config = analyze_to_config(SPA_HTML, "https://example.org/shop")
    clone = dict(config)
    assert verified_report(clone, SPA_HTML) is None


def test_remember_report_is_callable_standalone() -> None:
    """手工登记的报告也能取回（`remember_report` 是公开的小工具，不是私有 hack）。"""
    config = {"extract": {"mode": "html", "fields": {}}}
    remember_report(config, "<html></html>", {"mode": "html", "items": 1, "records": 1, "fields": {}})
    assert verified_report(config, "<html></html>") == {
        "mode": "html", "items": 1, "records": 1, "fields": {},
    }


def test_reusing_a_report_is_much_cheaper_than_reverifying() -> None:
    """量级护栏：复用应是"查一次字典"，重算则要重新解析整页。

    ★ 阈值取 10 倍余量：只守"确实没在重算"，不守具体毫秒数（CI 机器可能慢 5 倍）。
    """
    # 造一份「大但形态不变」的 SPA 页：条目多、结构不变，解析成本明显上去了。
    products = ",".join(
        f'{{"id":{index},"name":"P{index}","price":{index}.5}}'
        for index in range(1, 400)
    )
    html = (
        '<html><head><script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"products":[' + products + "]}}}"
        '</script></head><body><div id="__next"></div></body></html>'
    )
    config = analyze_to_config(html, "https://example.org/shop")
    started = time.perf_counter()
    reused = verified_report(config, html)
    reuse_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    verify_config(config, html)
    recompute_ms = (time.perf_counter() - started) * 1000
    assert reused is not None
    assert reuse_ms * 10 < recompute_ms, (
        f"复用 {reuse_ms:.3f} ms vs 重算 {recompute_ms:.3f} ms —— 看起来没跳过重算"
    )

"""内嵌数据链路的**用户可见结论**（2026-11-27）。

## 为什么单独钉

能力做出来但结论说错，等于没做。这条链路有两处极易说错：

1. **结论标签**：SSR 页面的 `source.kind` 也是 `static_html`，若按 kind 判就会报
   「静态可抓」—— 用户于是去改 CSS 选择器，而数据根本不在 DOM 里；
   反过来报「需浏览器渲染」也不对：这条路**不需要浏览器**（这正是收益所在）。
2. **交互告警**：SSR 站点的外壳同样是 SPA（`<div id="__next">`），
   若照旧告警"SPA 外壳，内容靠 JS 渲染 ⇒ 请用 record-actions 录一遍"，
   就是让用户给一件**已经完成**的事再付一次成本。
"""

from __future__ import annotations

from omnicrawler.extraction.intelligent_scraper.field_inference import _interaction_advice
from omnicrawler.extraction.intelligent_scraper.result_quality import (
    VERDICT_EMBEDDED,
    verdict,
)


def _embedded_config() -> dict:
    return {
        "source": {"kind": "static_html", "seeds": ["https://example.org/shop"]},
        "extract": {
            "mode": "html",
            "embedded_json": {"item_path": "$.props.pageProps.products[*]", "source": "script#__NEXT_DATA__"},
            "fields": {"名称": {"source": "embedded_json", "record_level": True, "path": "name"}},
        },
    }


def test_verdict_says_embedded_not_plain_static() -> None:
    """★ 归到「静态可抓」会误导用户去改选择器；归到「需浏览器」则抹掉了收益。"""
    label, why = verdict(config=_embedded_config(), records=3, signals=("spa_shell",), used_render=False)
    assert label == VERDICT_EMBEDDED, label
    assert "浏览器" in why and "不必" in why, why
    assert "embedded_json" in why, why


def test_plain_static_page_still_says_static() -> None:
    """反方向：没有内嵌数据的普通静态页，结论不许变成"内嵌"。"""
    config = {
        "source": {"kind": "static_html", "seeds": ["https://example.org/list"]},
        "extract": {"mode": "html", "item_selector": "div.item", "fields": {"标题": {"selector": "h3"}}},
    }
    label, _why = verdict(config=config, records=5, signals=(), used_render=False)
    assert label == "静态可抓", label


def test_spa_shell_advice_is_suppressed_when_embedded_data_worked() -> None:
    """★ 已经取到数据了，就不要再叫用户去 record-actions 录一遍。"""
    with_data = _interaction_advice(("spa_shell",), scroll_rounds=0, embedded_ok=True)
    assert not any("record-actions" in line for line in with_data), with_data
    assert not any("SPA 外壳" in line for line in with_data), with_data


def test_spa_shell_advice_still_fires_when_no_data_was_found() -> None:
    """反方向：没有内嵌数据时，这条告警必须还在（否则用户不知道该录动作）。"""
    without = _interaction_advice(("spa_shell",), scroll_rounds=0, embedded_ok=False)
    joined = "\n".join(without)
    assert "SPA 外壳" in joined, without
    assert "record-actions" in joined, without

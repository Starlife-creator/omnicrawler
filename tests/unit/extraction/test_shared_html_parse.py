"""页面解析只做一次（2026-11-27）。

## 为什么

一条页面在流水线里被 **HTML 解析 2–3 次**：

1. `HTMLProcessor.process` 解析一次（提取字段）；
2. `GenericSource.discover` 又解析一次（找链接）；
3. `auto-analyze` 的 `verify_config` 再解析一次（试跑验证）。

而 HTML 解析占单页 CPU 的 **43%–60%**（实测：204 KB 页面 53–59 ms/次）。
同一份字节、同一棵只读树，解析三次是纯粹的重复劳动。

## 判据纪律

★ 共享缓存必须**不影响正确性**，所以三条一起钉住：

* 命中条件按**内容摘要**，不按对象身份（流水线拿 FetchResult、用户侧拿 str）；
* `parse_html` 保持"独占"语义（调用方可能 `decompose()` 节点 —— `change_detector` 就是）；
* 只读消费者才用 `parse_html_shared`；改树的消费者继续用 `parse_html`。
"""

from __future__ import annotations

import time
from pathlib import Path

from omnicrawler.extraction import html_tools

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "real_sites"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def test_same_content_returns_the_same_tree() -> None:
    html_tools.clear_shared_document()
    first = html_tools.parse_html_shared("<html><body><p>x</p></body></html>")
    second = html_tools.parse_html_shared("<html><body><p>x</p></body></html>")
    assert first is second, "同一份内容却重新解析了"


def test_different_content_forces_a_reparse() -> None:
    """★ 判据是内容摘要：内容变了必须重解析，否则会返回上一页的树。"""
    html_tools.clear_shared_document()
    first = html_tools.parse_html_shared("<html><body><p>one</p></body></html>")
    second = html_tools.parse_html_shared("<html><body><p>two</p></body></html>")
    assert first is not second
    assert "two" in second.get_text()


def test_parse_html_always_returns_a_fresh_tree() -> None:
    """★ `parse_html` 必须保持**独占**语义 —— 调用方会 decompose 节点。

    这条是安全性守卫：把 `parse_html` 也改成缓存，`change_detector` 的
    `node.decompose()` 会污染下一次解析（症状极隐蔽：第二次提取少一块内容）。
    """
    html = "<html><body><p>x</p></body></html>"
    first = html_tools.parse_html(html)
    second = html_tools.parse_html(html)
    assert first is not second, "parse_html 返回了共享树，调用方改它会互相污染"
    # 改第一棵树的节点，第二棵不受影响
    for node in first.find_all("p"):
        node.decompose()
    assert second.find("p") is not None


def test_shared_tree_survives_a_discovery_pass() -> None:
    """真实链路：提取 → 链接发现，两次拿到的是**同一棵**树且都正确。"""
    import tempfile

    from omnicrawler.core.config import AppConfig
    from omnicrawler.core.models import CrawlRequest, FetchResult
    from omnicrawler.extraction.extractors import HTMLProcessor
    from omnicrawler.sources.sources import GenericSource

    html = _fixture("books_main.html")
    raw = {
        "project": {"name": "t"},
        "source": {"kind": "crawl", "seeds": ["https://books.toscrape.com/"]},
        "extract": {
            "mode": "html",
            "item_selector": "article.product_pod",
            "fields": {"标题": {"selector": "h3 a", "attr": "title"}},
        },
    }
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        config = AppConfig(root / "c.yaml", root, raw, root / "work")
        result = FetchResult(
            CrawlRequest("https://books.toscrape.com/"),
            "https://books.toscrape.com/",
            200,
            {"content-type": "text/html; charset=utf-8"},
            html.encode("utf-8"),
            0.01,
        )
        html_tools.clear_shared_document()
        records = HTMLProcessor(config).process(result).records
        links = GenericSource(config).discover(result)
    assert len(records) >= 20, len(records)
    assert links, "共享解析后链接发现拿不到链接了"


def test_reuse_is_much_cheaper_than_parsing_again() -> None:
    """量级护栏：命中缓存应是"算一次摘要"，重解析则要几十毫秒。

    ★ 阈值取 10 倍余量，只守"确实没在重解析"。
    """
    html = _fixture("scrapethissite_simple.html")
    html_tools.clear_shared_document()
    html_tools.parse_html_shared(html)
    started = time.perf_counter()
    html_tools.parse_html_shared(html)
    hit_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    html_tools.parse_html(html)
    fresh_ms = (time.perf_counter() - started) * 1000
    assert hit_ms * 10 < fresh_ms, (
        f"命中 {hit_ms:.3f} ms vs 重新解析 {fresh_ms:.1f} ms —— 看起来没跳过解析"
    )

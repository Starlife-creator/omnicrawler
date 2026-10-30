"""B-P2-1：字段级选择器隔离 —— 单条规则 CSS/XPath 语法异常按字段隔离，
失败字段记入 trace（错误类型 + 消息）并跳过，其余字段继续提取；
坏 item_selector 回退整文档提取，不中断整页。
"""

from __future__ import annotations

from typing import Any

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import HTMLProcessor


class _FakeConfig:
    def __init__(self, extract: dict[str, Any]) -> None:
        self._extract = extract

    def section(self, name: str) -> dict[str, Any]:
        return self._extract if name == "extract" else {}


def _result(html: str) -> FetchResult:
    request = CrawlRequest("https://example.org/")
    return FetchResult(
        request, "https://example.org/", 200,
        {"content-type": "text/html"}, html.encode("utf-8"), 0.1,
    )


def test_bad_css_field_isolated_others_still_extract() -> None:
    config = _FakeConfig({
        "mode": "html",
        "fields": {
            "标题": {"selector": "h1"},
            "坏字段": {"selector": "p:::"},
        },
    })
    processor = HTMLProcessor(config)
    result = processor.process(_result("<html><body><h1>标题A</h1><p>正文段落</p></body></html>"))

    assert len(result.records) == 1
    record = result.records[0]
    # 好字段照常提取
    assert record.data.get("标题") == "标题A"
    # 坏字段不进 data
    assert "坏字段" not in record.data
    # 失败字段 evidence 含错误类型与消息，置信度 0.0
    evidence = record.evidence["坏字段"]
    assert evidence["error_type"]
    assert evidence["error"]
    assert evidence["confidence"] == 0.0


def test_bad_xpath_field_isolated_others_still_extract() -> None:
    import pytest

    pytest.importorskip("lxml")
    config = _FakeConfig({
        "mode": "html",
        "fields": {
            "标题": {"selector": "h1"},
            "坏路径": {"xpath": "//div["},
        },
    })
    processor = HTMLProcessor(config)
    result = processor.process(_result("<html><body><h1>标题B</h1></body></html>"))

    assert len(result.records) == 1
    record = result.records[0]
    assert record.data.get("标题") == "标题B"
    assert "坏路径" not in record.data
    evidence = record.evidence["坏路径"]
    assert evidence["error_type"]
    assert evidence["error"]
    assert evidence["confidence"] == 0.0


def test_bad_item_selector_falls_back_to_document(caplog: Any) -> None:
    config = _FakeConfig({
        "mode": "html",
        "item_selector": "div >> x",
        "fields": {"标题": {"selector": "h1"}},
    })
    processor = HTMLProcessor(config)
    result = processor.process(_result("<html><body><h1>标题C</h1></body></html>"))

    # 坏 item_selector 回退整文档，仍产出一条记录
    assert len(result.records) == 1
    assert result.records[0].data.get("标题") == "标题C"
    assert any("item_selector" in record.message for record in caplog.records)

"""批 C-1 文档槽位抽取（doc_extractors/）单元测试。

覆盖：文本（regex/text）、JSON（jsonpath/regex）、HTML（css/regex/jsonpath 走
JSON-LD）、统一入口分派、PDF 缺依赖报错。
"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.doc_extractors import extract_slots
from omnicrawler.doc_extractors.base import JSONDocExtractor, TextDocExtractor
from omnicrawler.doc_extractors.html import HTMLDocExtractor
from omnicrawler.state.scene_store import SlotDefinition


def _slot(key: str, extractor: str, pattern: str) -> SlotDefinition:
    return SlotDefinition(scene="t", slot_key=key, extractor_type=extractor, pattern=pattern)


def test_text_regex_slot_extracts_capture_group() -> None:
    definitions = [_slot("company", "regex", r"公司[：:]\s*([^\s，。]+)")]
    hits = TextDocExtractor().extract("公司：星辰科技有限公司 2026 年报告", definitions)
    assert len(hits) == 1
    assert hits[0].slot_key == "company"
    assert hits[0].value == "星辰科技有限公司"
    assert hits[0].confidence == 1.0


def test_text_slot_contains_match() -> None:
    definitions = [_slot("keyword", "text", "年度报告")]
    hits = TextDocExtractor().extract("这是一份年度报告文档", definitions)
    assert len(hits) == 1
    assert hits[0].value == "年度报告"


def test_text_regex_no_match_returns_empty() -> None:
    definitions = [_slot("company", "regex", r"公司[：:]\s*([^\s，。]+)")]
    hits = TextDocExtractor().extract("没有公司字段", definitions)
    assert hits == []


def test_json_jsonpath_slot() -> None:
    payload = {"data": {"items": [{"title": "A"}, {"title": "B"}]}}
    definitions = [_slot("first", "jsonpath", "$.data.items[0].title")]
    hits = JSONDocExtractor().extract(payload, definitions)
    assert len(hits) == 1
    assert hits[0].value == "A"
    assert hits[0].evidence["matches"] == 1


def test_json_regex_slot_on_serialized_payload() -> None:
    payload = {"company": "星辰科技"}
    definitions = [_slot("company", "regex", r'"company"\s*:\s*"([^"]+)"')]
    hits = JSONDocExtractor().extract(payload, definitions)
    assert len(hits) == 1
    assert hits[0].value == "星辰科技"


def test_html_css_slot() -> None:
    html = "<html><body><h1>标题A</h1></body></html>"
    definitions = [_slot("title", "css", "h1")]
    hits = HTMLDocExtractor().extract(html, definitions)
    assert len(hits) == 1
    assert hits[0].value == "标题A"


def test_html_regex_slot() -> None:
    html = "<html><body><p>营业收入 1,234 万元</p></body></html>"
    definitions = [_slot("revenue", "regex", r"营业收入\s*([\d,]+)")]
    hits = HTMLDocExtractor().extract(html, definitions)
    assert len(hits) == 1
    assert hits[0].value == "1,234"


def test_html_jsonld_slot() -> None:
    html = (
        "<html><head>"
        '<script type="application/ld+json">{"headline": "新闻标题", "org": {"name": "星辰"}}'
        "</script></head><body></body></html>"
    )
    definitions = [_slot("headline", "jsonpath", "$.headline")]
    hits = HTMLDocExtractor().extract(html, definitions)
    assert len(hits) == 1
    assert hits[0].value == "新闻标题"


def test_extract_slots_dispatch_by_type() -> None:
    definitions = [_slot("company", "regex", r"公司[：:]\s*([^\s，。]+)")]
    text_hits = extract_slots("公司：星辰", definitions, document_type="text")
    assert len(text_hits) == 1
    json_hits = extract_slots(
        json.dumps({"company": "星辰"}),
        [_slot("company", "jsonpath", "$.company")],
        document_type="json",
    )
    assert len(json_hits) == 1
    html_hits = extract_slots(
        "<html><body><h1>x</h1></body></html>",
        [_slot("title", "css", "h1")],
        document_type="html",
    )
    assert len(html_hits) == 1


def test_pdf_extractor_requires_pymupdf() -> None:
    import importlib.util

    import pytest

    from omnicrawler.doc_extractors.pdf import PDFDocExtractor

    # 环境感知：无 pdfplumber 时构造应报带安装提示的错误；有依赖则正常构造
    if importlib.util.find_spec("pdfplumber") is None:
        with pytest.raises(RuntimeError, match="pdfplumber"):
            PDFDocExtractor()
    else:
        PDFDocExtractor()  # 不应抛错


def test_extract_slots_auto_document(tmp_path) -> None:
    """auto 模式：富文档经 document_ir 解析为文本后走 TextDocExtractor。"""
    import pytest

    pytest.importorskip("docx")
    import docx

    src = Path(tmp_path) / "r.docx"
    document = docx.Document()
    document.add_paragraph("公司名称：星辰科技有限公司")
    document.save(str(src))

    definitions = [_slot("company", "regex", r"公司名称[：:]\s*([^\s，。]+)")]
    hits = extract_slots(str(src), definitions, document_type="auto")
    assert len(hits) == 1
    assert hits[0].value == "星辰科技有限公司"
    assert hits[0].confidence == 1.0

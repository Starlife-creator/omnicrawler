"""S1：document_ir 统一文档中间表示 —— txt/html/eml 标准库解析 + 导出 + 进度。

chardet 缺失时仅跳过编码检测用例，其余（utf-8 解析/HTML/EML/导出/进度）不依赖 chardet。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from omnicrawl.core.encoding import detect_encoding, smart_decode
from omnicrawl.document_ir import DOCUMENT_PARSERS, DocumentIR, parse_document, sniff_document_format
from omnicrawl.services.progress import TaskProgressEvent


# ── Fixtures ─────────────────────────────────────────────
@pytest.fixture()
def txt_utf8(tmp_path: Path) -> Path:
    p = tmp_path / "note.txt"
    p.write_text("我的标题\n\n这是第一段内容。\n继续第一段。\n\n第二段内容。", encoding="utf-8")
    return p


@pytest.fixture()
def html_doc(tmp_path: Path) -> Path:
    p = tmp_path / "page.htm"
    p.write_text(
        "<html><head><title>测试页</title>"
        '<meta name="description" content="desc-1"></head>'
        "<body><h1>主标题</h1><p>段落一</p><p>段落二</p>"
        '<a href="https://example.com/a">链接A</a>'
        "<script>var x=1;</script></body></html>",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def eml_doc(tmp_path: Path) -> Path:
    p = tmp_path / "mail.eml"
    subject = "=?utf-8?B?" + base64.b64encode("测试主题".encode()).decode("ascii") + "?="
    content = (
        "From: sender@example.com\r\n"
        "To: me@example.com\r\n"
        f"Subject: {subject}\r\n"
        "Date: Mon, 12 Aug 2026 10:00:00 +0800\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: 8bit\r\n"
        "\r\n"
        "这是邮件正文。"
    )
    p.write_bytes(content.encode("utf-8"))
    return p


# ── sniff_document_format ────────────────────────────────
def test_sniff_supported() -> None:
    assert sniff_document_format(Path("a.txt")) == ".txt"
    assert sniff_document_format(Path("a.html")) == ".html"
    assert sniff_document_format(Path("a.htm")) == ".html"  # alias 归一化
    assert sniff_document_format(Path("a.eml")) == ".eml"


def test_sniff_unsupported() -> None:
    assert sniff_document_format(Path("a.xyz")) is None
    assert sniff_document_format(Path("noext")) is None


def test_registry_has_stdlib_parsers() -> None:
    for ext in (".txt", ".html", ".eml"):
        assert ext in DOCUMENT_PARSERS


# ── parse_document: .txt ─────────────────────────────────
def test_parse_txt_utf8(txt_utf8: Path) -> None:
    doc = parse_document(txt_utf8)
    assert isinstance(doc, DocumentIR)
    assert doc.kind == ".txt"
    assert doc.title == "我的标题"
    assert doc.paragraphs == ["这是第一段内容。\n继续第一段。", "第二段内容。"]


def test_parse_txt_gbk(tmp_path: Path) -> None:
    pytest.importorskip("chardet")
    p = tmp_path / "gbk.txt"
    # 加长文本提高 chardet 置信度（短文本易低于 0.80 阈值回退 utf-8）
    text = "公司名称：测试科技有限公司\n\n" + ("营业收入：1234567.89 亿元\n" * 20)
    p.write_bytes(text.encode("gbk"))
    doc = parse_document(p)
    assert "营业收入" in doc.paragraphs[0]


def test_parse_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_document(tmp_path / "nope.txt")


def test_parse_unsupported_format(tmp_path: Path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(b"\x00\x01")
    with pytest.raises(ValueError, match="不支持的文档格式"):
        parse_document(p)


# ── parse_document: .html ────────────────────────────────
def test_parse_html(html_doc: Path) -> None:
    doc = parse_document(html_doc)
    assert doc.kind == ".html"
    assert doc.title == "主标题"
    assert "段落一" in doc.paragraphs
    assert "段落二" in doc.paragraphs
    assert ("链接A", "https://example.com/a") in doc.links
    assert doc.metadata.get("description") == "desc-1"


# ── T3：HTML 正文主体抽取（容器命中 / 回退 / 关闭）────────
def test_parse_html_main_container_skips_navigation(tmp_path: Path) -> None:
    """命中 main 容器：导航/页脚 p 不应进入正文。"""
    p = tmp_path / "article.html"
    p.write_text(
        "<html><body>"
        "<nav><p>导航链接</p></nav>"
        '<main><h1>文章标题</h1><p>正文段落A</p><p>正文段落B</p></main>'
        "<footer><p>页脚版权</p></footer>"
        "</body></html>",
        encoding="utf-8",
    )
    doc = parse_document(p)
    assert doc.metadata.get("main_container") is True
    assert "正文段落A" in doc.paragraphs
    assert "正文段落B" in doc.paragraphs
    assert not any("导航" in para for para in doc.paragraphs)
    assert not any("页脚" in para for para in doc.paragraphs)


def test_parse_html_main_container_fallback_when_missing(tmp_path: Path) -> None:
    """无任何容器结构时回退全页选择（保持原行为）。"""
    p = tmp_path / "flat.html"
    p.write_text(
        "<html><body><h1>标题</h1><p>全页段落一</p><p>全页段落二</p></body></html>",
        encoding="utf-8",
    )
    doc = parse_document(p)
    assert "main_container" not in doc.metadata
    assert "全页段落一" in doc.paragraphs
    assert "全页段落二" in doc.paragraphs


def test_parse_html_main_container_disabled_by_option(tmp_path: Path) -> None:
    """options['main_content']=False 时保留全页选择，不限定容器。"""
    p = tmp_path / "mixed.html"
    p.write_text(
        "<html><body>"
        '<main><p>容器内段落</p></main>'
        "<p>容器外段落</p>"
        "</body></html>",
        encoding="utf-8",
    )
    doc = parse_document(p, options={"main_content": False})
    assert "main_container" not in doc.metadata
    assert "容器内段落" in doc.paragraphs
    assert "容器外段落" in doc.paragraphs


# ── parse_document: .eml ─────────────────────────────────
def test_parse_eml(eml_doc: Path) -> None:
    doc = parse_document(eml_doc)
    assert doc.kind == ".eml"
    assert doc.title == "测试主题"
    assert doc.metadata["headers"]["from"] == "sender@example.com"
    assert any("这是邮件正文" in p for p in doc.paragraphs)


# ── on_progress ──────────────────────────────────────────
def test_on_progress_finished(txt_utf8: Path) -> None:
    events: list[TaskProgressEvent] = []

    def _collect(ev: TaskProgressEvent) -> None:
        events.append(ev)

    parse_document(txt_utf8, on_progress=_collect)
    assert events, "应至少收到一个进度事件"
    # 存在 running 事件带 parse 阶段，最终事件 finished 100%（finish 后 stage 清空）
    assert any(ev.stage == "parse" for ev in events)
    assert events[-1].state == "finished"
    assert events[-1].percent == 100.0


# ── 导出视图 ─────────────────────────────────────────────
def test_to_text(txt_utf8: Path) -> None:
    doc = parse_document(txt_utf8)
    text = doc.to_text()
    assert "我的标题" in text
    assert "这是第一段内容" in text
    assert "第二段内容" in text


def test_to_markdown_html(html_doc: Path) -> None:
    doc = parse_document(html_doc)
    md = doc.to_markdown()
    assert md.startswith("# 主标题")
    assert "段落一" in md


def test_to_markdown_table(tmp_path: Path) -> None:
    doc = DocumentIR(
        source=tmp_path / "t.txt",
        kind=".txt",
        title="表格文档",
        tables=[[["a", "b"], ["1", "2"]]],  # list[list[list[str]]]：一张两行两列的表
    )
    md = doc.to_markdown()
    assert "| a | b |" in md
    assert "| 1 | 2 |" in md


# ── core.encoding ────────────────────────────────────────
def test_smart_decode_utf8() -> None:
    text, enc = smart_decode("中文内容".encode())
    assert text == "中文内容"
    assert enc.lower().replace("_", "-") in ("utf-8", "utf8")


def test_smart_decode_gbk_with_chardet() -> None:
    pytest.importorskip("chardet")
    text = "中文GBK编码测试，" * 20  # 加长提高置信度
    data = text.encode("gbk")
    decoded, enc = smart_decode(data)
    assert decoded == text
    assert enc.lower() != "utf-8"  # chardet 应识别为 GBK/GB2312，而非 utf-8


def test_detect_encoding_fallback_without_chardet(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    # sys.modules 中值为 None 时 import 会抛 ImportError → 走 fallback 分支
    monkeypatch.setitem(sys.modules, "chardet", None)
    assert detect_encoding(b"\xff\xfe\x00", fallback="utf-8") == "utf-8"

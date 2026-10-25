"""B-P2-14b：纯 HTML 邮件无 text/plain 时降级为 HTML 文本提取，body_source 留痕。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.document_ir import parse_document


def test_plain_text_email_marks_plain_source(tmp_path: Path) -> None:
    eml = tmp_path / "plain.eml"
    eml.write_bytes(
        (
            "From: sender@example.com\r\n"
            "To: me@example.com\r\n"
            "Subject: 纯文本邮件\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "这是纯文本正文。"
        ).encode("utf-8"),
    )
    doc = parse_document(eml)
    assert doc.metadata["body_source"] == "plain"
    assert any("纯文本正文" in part for part in doc.paragraphs)


def test_html_only_email_falls_back_to_plain_text(tmp_path: Path) -> None:
    eml = tmp_path / "html-only.eml"
    eml.write_bytes(
        (
            "From: sender@example.com\r\n"
            "To: me@example.com\r\n"
            "Subject: 纯HTML邮件\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><body><p>这是HTML正文内容，包含<b>加粗</b>片段。</p></body></html>"
        ).encode("utf-8"),
    )
    doc = parse_document(eml)
    assert doc.metadata["body_source"] == "html_fallback"
    assert any("HTML正文内容" in part for part in doc.paragraphs)

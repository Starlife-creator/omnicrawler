"""S2.5.28：markdown_exporter 对 dict 证据安全截断。"""

from __future__ import annotations

from omnicrawler.export.markdown_exporter import MarkdownExporter


def test_render_card_with_dict_evidence() -> None:
    lines = MarkdownExporter._render_card([
        {
            "field_name": "金额",
            "normalized_value": "1200000",
            "confidence": 0.9,
            "evidence": {"raw": "Revenue: 1,200,000", "page": 1, "note": "x" * 500},
        }
    ])
    assert any("证据" in line for line in lines)
    evidence_line = next(line for line in lines if line.startswith("**证据**"))
    assert len(evidence_line) <= 200 + len("**证据**: ")


def test_render_card_with_string_evidence() -> None:
    lines = MarkdownExporter._render_card([
        {"field_name": "日期", "value": "2024-03-01", "evidence": "page 1"}
    ])
    assert any("page 1" in line for line in lines)

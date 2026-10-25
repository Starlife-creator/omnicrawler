"""B-P2-12：Markdown 导出转义补齐、非数值置信度容错、行级流式写出回归。"""

from __future__ import annotations

import csv
from pathlib import Path

from omnicrawler.export.markdown_exporter import MarkdownExporter


def test_row_cell_escapes_special_chars() -> None:
    row = {"col": "a|b # c * d [e] f `g` \\ h"}
    md = MarkdownExporter._row_to_md(["col"], row)
    assert r"a\|b" in md
    assert r"\# c" in md
    assert r"\* d" in md
    assert r"\[e\]" in md
    assert r"\`g\`" in md
    assert r"\\ h" in md


def test_card_escapes_name_and_value_and_skips_bad_confidence() -> None:
    lines = MarkdownExporter._render_card([
        {"field_name": "*名称*", "normalized_value": "`代码`", "confidence": "high"},
        {"field_name": "ok", "value": "plain", "confidence": 0.5},
    ])
    text = "\n".join(lines)
    assert r"\*名称\*" in text
    assert r"\`代码\`" in text
    # 首条非数值置信度被跳过（不抛异常），第二条正常格式化。
    first_block = text.split("### ok")[0]
    assert "置信度" not in first_block
    assert "50%" in text


def test_table_bad_confidence_uses_placeholder() -> None:
    lines = MarkdownExporter._render_table([{"field_name": "x", "value": "v", "confidence": "n/a"}])
    assert "—" in "\n".join(lines)


def test_export_results_streaming_escapes_headers_and_rows(tmp_path: Path) -> None:
    csv_p = tmp_path / "r.csv"
    with csv_p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["#标题", "值"])
        writer.writeheader()
        writer.writerow({"#标题": "A*", "值": "[链接](http://x)"})
    out = MarkdownExporter.export_results(csv_p)
    md = out.read_text(encoding="utf-8")
    assert r"\#标题" in md
    assert r"A\*" in md
    assert r"\[链接\]" in md


def test_single_record_bad_confidence_does_not_crash() -> None:
    md = MarkdownExporter.export_single_record({
        "record_id": "r1",
        "field_values": [{"field_name": "x", "value": "v", "confidence": "high"}],
    })
    assert "置信度" not in md

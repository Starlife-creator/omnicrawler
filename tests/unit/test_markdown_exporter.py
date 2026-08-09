"""Tests for export.markdown_exporter — pure string manipulation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from omnicrawl.export.markdown_exporter import MarkdownExporter

# ── helpers ────────────────────────────────────────────────────────────

def _make_csv(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    p = tmp_path / "results.csv"
    headers = list(rows[0].keys())
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    return p


def _make_jsonl(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "records.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return p


# ── export_results ─────────────────────────────────────────────────────

class TestExportResults:
    def test_basic_csv(self, tmp_path: Path) -> None:
        csv_p = _make_csv(tmp_path, [{"name": "Alice", "score": "95"}, {"name": "Bob", "score": "87"}])
        out = MarkdownExporter.export_results(csv_p)
        assert out.exists()
        md = out.read_text(encoding="utf-8")
        assert "# Crawl Results" in md
        assert "Alice" in md
        assert "Bob" in md
        assert "**Total records**: 2" in md

    def test_max_records_cap(self, tmp_path: Path) -> None:
        csv_p = _make_csv(tmp_path, [{"n": "1"}, {"n": "2"}, {"n": "3"}])
        out = MarkdownExporter.export_results(csv_p, max_records=2)
        md = out.read_text(encoding="utf-8")
        assert "**Total records**: 2" in md

    def test_empty_csv(self, tmp_path: Path) -> None:
        """Empty CSV (no data rows) produces a valid empty report."""
        csv_p = tmp_path / "empty.csv"
        csv_p.write_text("name\n", encoding="utf-8")  # header only, no data rows
        out = MarkdownExporter.export_results(csv_p)
        md = out.read_text(encoding="utf-8")
        assert "_No results._" in md

    def test_with_jsonl_evidence(self, tmp_path: Path) -> None:
        csv_p = _make_csv(tmp_path, [{"name": "Alice", "record_id": "rec1"}])
        jl_p = _make_jsonl(tmp_path, [{"record_id": "rec1", "field_values": {"x": 1}}])
        out = MarkdownExporter.export_results(csv_p, jsonl_path=jl_p)
        md = out.read_text(encoding="utf-8")
        assert "```json" in md
        assert '"record_id": "rec1"' in md

    def test_groupby(self, tmp_path: Path) -> None:
        csv_p = _make_csv(tmp_path, [
            {"name": "A", "cat": "X"}, {"name": "B", "cat": "X"}, {"name": "C", "cat": "Y"},
        ])
        out = MarkdownExporter.export_results(csv_p, group_by="cat")
        md = out.read_text(encoding="utf-8")
        assert "## X (2 records)" in md
        assert "## Y (1 records)" in md

    def test_output_path_argument(self, tmp_path: Path) -> None:
        csv_p = _make_csv(tmp_path, [{"name": "A"}])
        custom = tmp_path / "custom_output.md"
        out = MarkdownExporter.export_results(csv_p, output_path=custom)
        assert out == custom
        assert custom.exists()

    def test_no_evidence_when_disabled(self, tmp_path: Path) -> None:
        csv_p = _make_csv(tmp_path, [{"name": "Alice", "record_id": "rec1"}])
        jl_p = _make_jsonl(tmp_path, [{"record_id": "rec1", "field_values": {"x": 1}}])
        out = MarkdownExporter.export_results(csv_p, jsonl_path=jl_p, include_evidence=False)
        md = out.read_text(encoding="utf-8")
        assert "```json" not in md


# ── export_single_record ───────────────────────────────────────────────

class TestExportSingleRecord:
    def test_card_style(self) -> None:
        rec = {
            "record_id": "abc",
            "source_url": "https://example.com",
            "field_values": [
                {"field_name": "title", "normalized_value": "Hello", "confidence": 0.95},
                {"field_name": "price", "value": "$10"},
            ],
        }
        md = MarkdownExporter.export_single_record(rec, style="card")
        assert "# Record: abc" in md
        assert "**值**: Hello" in md
        assert "**置信度**: 95%" in md
        assert "**值**: $10" in md

    def test_table_style(self) -> None:
        rec = {
            "record_id": "abc",
            "source_url": "https://example.com",
            "fields": {"title": "Hello", "score": "100"},
        }
        md = MarkdownExporter.export_single_record(rec, style="table")
        assert "| 字段 | 值 | 置信度 |" in md

    def test_list_style(self) -> None:
        rec = {
            "record_id": "abc",
            "source_url": "https://example.com",
            "fields": {"title": "Hello", "score": "100"},
        }
        md = MarkdownExporter.export_single_record(rec, style="list")
        assert "- **title**: Hello" in md
        assert "- **score**: 100" in md

    def test_with_output_path(self, tmp_path: Path) -> None:
        rec = {"record_id": "abc", "source_url": "https://x.com", "fields": {"a": "1"}}
        out = tmp_path / "single.md"
        MarkdownExporter.export_single_record(rec, output_path=out)
        assert out.exists()
        assert "abc" in out.read_text(encoding="utf-8")

    def test_empty_fields(self) -> None:
        rec = {"record_id": "abc"}
        md = MarkdownExporter.export_single_record(rec)
        assert "# Record: abc" in md

    def test_slash_in_url_render(self) -> None:
        rec = {"record_id": "abc", "url": "https://x.com", "fields": {"raw_url": "https://x.com"}}
        md = MarkdownExporter.export_single_record(rec, style="list")
        assert "https://x.com" in md


# ── internal helpers ───────────────────────────────────────────────────

class TestLoadJsonlIndex:
    def test_valid_jsonl(self, tmp_path: Path) -> None:
        p = _make_jsonl(tmp_path, [
            {"record_id": "r1", "data": 1},
            {"record_id": "r2", "data": 2},
        ])
        idx = MarkdownExporter._load_jsonl_index(p)
        assert idx["r1"] == {"record_id": "r1", "data": 1}
        assert idx["r2"] == {"record_id": "r2", "data": 2}

    def test_missing_file(self) -> None:
        idx = MarkdownExporter._load_jsonl_index(Path("/nonexistent.jsonl"))
        assert idx == {}

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.jsonl"
        p.write_text("not json\n", encoding="utf-8")
        idx = MarkdownExporter._load_jsonl_index(p)
        assert idx == {}  # logs warning but doesn't crash


class TestBuildMarkdown:
    def test_pipe_escaping(self) -> None:
        """_row_to_md escapes pipe characters in cell values."""
        row = {"col": "value|with|pipes"}
        md = MarkdownExporter._row_to_md(["col"], row)
        assert "value\\|with\\|pipes" in md


class TestRenderCard:
    def test_with_evidence(self) -> None:
        fields = [{"field_name": "x", "value": "y", "evidence": "some evidence here"}]
        lines = MarkdownExporter._render_card(fields)
        text = "\n".join(lines)
        assert "**证据**: some evidence here" in text

    def test_confidence_formatting(self) -> None:
        fields = [{"field_name": "x", "value": "y", "confidence": 0.853}]
        lines = MarkdownExporter._render_card(fields)
        text = "\n".join(lines)
        assert "85%" in text


class TestRenderTable:
    def test_without_confidence(self) -> None:
        fields = [{"field_name": "x", "value": "y"}]
        lines = MarkdownExporter._render_table(fields)
        text = "\n".join(lines)
        assert "—" in text  # confidence placeholder

    def test_long_value_truncated(self) -> None:
        fields = [{"field_name": "x", "value": "a" * 100, "confidence": 1.0}]
        lines = MarkdownExporter._render_table(fields)
        text = "\n".join(lines)
        assert "a" * 80 in text


class TestRenderList:
    def test_basic(self) -> None:
        fields = [{"field_name": "title", "normalized_value": "Hello"}]
        lines = MarkdownExporter._render_list(fields)
        assert lines == ["- **title**: Hello", ""]

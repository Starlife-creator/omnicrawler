"""Markdown exporter: convert crawl results to structured Markdown files.

Supports both batch export (CSV/JSONL → .md) and single-record export
(card / table / list styles).  Integrates with the GUI via ResultTable and
EvidenceView export buttons.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


class MarkdownExporter:
    """Crawl results → Markdown converter."""

    # ── public API ──────────────────────────────────────────────

    @staticmethod
    def export_results(
        csv_path: Path,
        jsonl_path: Path | None = None,
        output_path: Path | None = None,
        *,
        include_evidence: bool = True,
        group_by: str | None = None,
        max_records: int = 0,
    ) -> Path:
        """Export a full CSV result file (and optional JSONL evidence) to Markdown.

        Args:
            csv_path: Path to the results CSV.
            jsonl_path: Optional records.jsonl for per-row evidence (appended after each row).
            output_path: Destination .md file; defaults to csv_path with .md extension.
            include_evidence: When True and jsonl_path is provided, embed the JSON
                evidence block after each record's table row.
            group_by: CSV column name to group records under H2 sections.
            max_records: Cap on exported records (0 = unlimited).

        Returns:
            The path of the written Markdown file.
        """
        output = output_path or csv_path.with_suffix(".md")

        # Load CSV
        rows: list[dict[str, str]] = []
        headers: Sequence[str] = []
        with csv_path.open("r", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            for row in reader:
                rows.append(row)
                if max_records and len(rows) >= max_records:
                    break

        # Load JSONL evidence (indexed by record_id)
        evidence_index: dict[str, dict[str, Any]] = {}
        if include_evidence and jsonl_path and jsonl_path.is_file():
            evidence_index = MarkdownExporter._load_jsonl_index(jsonl_path)

        md = MarkdownExporter._build_markdown(
            headers=headers,
            rows=rows,
            evidence_index=evidence_index,
            group_by=group_by,
            include_evidence=include_evidence,
        )

        output.write_text(md, encoding="utf-8")
        _logger.info("Markdown exported → %s (%d records)", output, len(rows))
        return output

    @staticmethod
    def export_single_record(
        record: dict[str, Any],
        output_path: Path | None = None,
        *,
        style: str = "card",
    ) -> str:
        """Convert a single JSONL record to Markdown.

        Args:
            record: A dict from records.jsonl (may include field_values, source_url, etc.).
            output_path: If given, writes the markdown to this file.
            style: Rendering style — ``"card"``, ``"table"``, or ``"list"``.

        Returns:
            The rendered Markdown string.
        """
        record_id = str(record.get("record_id", "—"))
        source_url = str(record.get("source_url", record.get("url", "—")))
        field_values = record.get("field_values", record.get("fields", {}))

        lines: list[str] = []
        lines.append(f"# Record: {record_id[:60]}")
        lines.append("")
        lines.append(f"- **Source**: {source_url}")
        lines.append(f"- **Record ID**: `{record_id}`")
        lines.append("")

        if isinstance(field_values, list):
            fields = field_values
        elif isinstance(field_values, dict):
            fields = [{"field_name": k, "value": v} for k, v in field_values.items()]
        else:
            fields = []

        if style == "card":
            lines.extend(MarkdownExporter._render_card(fields))
        elif style == "table":
            lines.extend(MarkdownExporter._render_table(fields))
        else:
            lines.extend(MarkdownExporter._render_list(fields))

        md = "\n".join(lines) + "\n"

        if output_path:
            output_path.write_text(md, encoding="utf-8")

        return md

    # ── internal helpers ────────────────────────────────────────

    @staticmethod
    def _load_jsonl_index(jsonl_path: Path) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        try:
            with jsonl_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    rid = rec.get("record_id")
                    if rid:
                        index[str(rid)] = rec
        except Exception:
            _logger.warning("Failed to load JSONL evidence from %s", jsonl_path)
        return index

    @staticmethod
    def _build_markdown(
        headers: Sequence[str],
        rows: list[dict[str, str]],
        evidence_index: dict[str, dict[str, Any]],
        group_by: str | None,
        include_evidence: bool,
    ) -> str:
        lines: list[str] = []
        lines.append("# Crawl Results")
        lines.append("")
        lines.append(f"**Total records**: {len(rows)}")
        lines.append("")

        if not rows:
            lines.append("_No results._")
            return "\n".join(lines) + "\n"

        # Table header
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")

        # Grouped or flat
        if group_by and group_by in headers:
            groups: dict[str, list[dict[str, str]]] = {}
            for row in rows:
                key = row.get(group_by, "—")
                groups.setdefault(key, []).append(row)

            for group_key in sorted(groups):
                group_rows = groups[group_key]
                lines.append("")
                lines.append(f"## {group_key} ({len(group_rows)} records)")
                lines.append("")
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in group_rows:
                    lines.append(MarkdownExporter._row_to_md(headers, row))
                    if include_evidence:
                        rid = row.get("record_id", "")
                        if rid and rid in evidence_index:
                            lines.append("")
                            lines.append("```json")
                            lines.append(json.dumps(evidence_index[rid], ensure_ascii=False, indent=2, default=str))
                            lines.append("```")
                            lines.append("")
        else:
            for row in rows:
                lines.append(MarkdownExporter._row_to_md(headers, row))
                if include_evidence:
                    rid = row.get("record_id", "")
                    if rid and rid in evidence_index:
                        lines.append("")
                        lines.append("```json")
                        lines.append(json.dumps(evidence_index[rid], ensure_ascii=False, indent=2, default=str))
                        lines.append("```")
                        lines.append("")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _row_to_md(headers: Sequence[str], row: dict[str, str]) -> str:
        vals = [row.get(h, "").replace("|", "\\|") for h in headers]
        return "| " + " | ".join(vals) + " |"

    @staticmethod
    def _render_card(fields: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for f in fields:
            name = f.get("field_name", f.get("name", "?"))
            value = f.get("normalized_value", f.get("value", ""))
            conf = f.get("confidence")
            lines.append(f"### {name}")
            lines.append("")
            lines.append(f"**值**: {value}")
            if conf is not None:
                pct = f"{float(conf):.0%}"
                lines.append(f"**置信度**: {pct}")
            if f.get("evidence"):
                lines.append(f"**证据**: {f['evidence'][:200]}")
            lines.append("")
        return lines

    @staticmethod
    def _render_table(fields: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        lines.append("| 字段 | 值 | 置信度 |")
        lines.append("|------|-----|--------|")
        for f in fields:
            name = f.get("field_name", f.get("name", "?"))
            value = str(f.get("normalized_value", f.get("value", "")))
            conf = f.get("confidence")
            pct = f"{float(conf):.0%}" if conf is not None else "—"
            lines.append(f"| {name} | {value[:80]} | {pct} |")
        lines.append("")
        return lines

    @staticmethod
    def _render_list(fields: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for f in fields:
            name = f.get("field_name", f.get("name", "?"))
            value = f.get("normalized_value", f.get("value", ""))
            lines.append(f"- **{name}**: {value}")
        lines.append("")
        return lines

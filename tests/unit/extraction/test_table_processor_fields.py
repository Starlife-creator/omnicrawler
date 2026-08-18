"""S2.5.32：TableProcessor 尊重 extract.fields。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import TableProcessor


def _config(tmp_path: Path, *, fields: dict | None = None) -> Path:
    path = tmp_path / "task.yaml"
    field_block = ""
    if fields:
        import yaml

        dump = yaml.dump(fields, allow_unicode=True, default_flow_style=False)
        field_block = "  fields:\n" + "".join(f"    {line}\n" for line in dump.splitlines())
    path.write_text(
        "project: {name: tproc, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "extract:\n  mode: table\n" + field_block,
        encoding="utf-8",
    )
    return path


def _result() -> FetchResult:
    body = (
        "<table>"
        "<tr><th>公司</th><th>金额</th><th>日期</th></tr>"
        "<tr><td>甲公司</td><td>1,200,000</td><td>2024-03-01</td></tr>"
        "<tr><td>乙公司</td><td>800,000</td><td>2024-04-02</td></tr>"
        "</table>"
    ).encode()
    request = CrawlRequest("https://example.org/")
    return FetchResult(request, request.url, 200, {"content-type": "text/html"}, body, 0.1)


def test_table_processor_uses_configured_fields(tmp_path: Path) -> None:
    processor = TableProcessor(load_config(_config(
        tmp_path,
        fields={"company": {"column": "公司"}, "amount": {"column": "金额"}},
    )))
    outcome = processor.process(_result())
    assert len(outcome.records) == 2
    assert outcome.records[0].data == {"company": "甲公司", "amount": "1,200,000"}
    assert outcome.records[1].data == {"company": "乙公司", "amount": "800,000"}


def test_table_processor_fields_by_index_and_selector(tmp_path: Path) -> None:
    processor = TableProcessor(load_config(_config(
        tmp_path,
        fields={"date": {"column": 2}, "company": {"selector": "td:nth-child(1)"}},
    )))
    outcome = processor.process(_result())
    assert outcome.records[0].data == {"date": "2024-03-01", "company": "甲公司"}


def test_table_processor_string_shorthand_means_header(tmp_path: Path) -> None:
    processor = TableProcessor(load_config(_config(
        tmp_path,
        fields={"amount": "金额"},
    )))
    outcome = processor.process(_result())
    assert outcome.records[0].data == {"amount": "1,200,000"}


def test_table_processor_without_fields_keeps_legacy_behavior(tmp_path: Path) -> None:
    processor = TableProcessor(load_config(_config(tmp_path)))
    outcome = processor.process(_result())
    assert "公司" in outcome.records[0].data
    assert "金额" in outcome.records[0].data

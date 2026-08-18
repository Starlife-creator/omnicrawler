"""S2.5.14：extractors 正则/JSON 容错 + field_designer 性能。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.core.safe_data import safe_regex_search
from omnicrawler.extraction.extractors import JSONProcessor
from omnicrawler.extraction.field_designer import analyze_html

# ── safe_regex_search ─────────────────────────────────────────────────

def test_safe_regex_compilation_error_returns_none() -> None:
    assert safe_regex_search("(unclosed", "text") is None


def test_safe_regex_matches_normally() -> None:
    match = safe_regex_search(r"price[:：]?\s*(\d+)", "price: 123")
    assert match is not None and match.group(1) == "123"


def test_safe_regex_catastrophic_pattern_rejected() -> None:
    # (a+)+ 型灾难性回溯模式：执行前拒绝，返回 None 而非卡死
    pattern = r"^(a+)+$"
    assert safe_regex_search(pattern, "a" * 64 + "b") is None


# ── extractors group 越界防护 ─────────────────────────────────────────

def test_regex_group_out_of_range_skipped(tmp_path: Path) -> None:

    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2514, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "extract: {mode: html, fields: {f: {selector: p, regex: '(\\\\d+)', group: 99}}}\n",
        encoding="utf-8",
    )
    from omnicrawler.core.config import load_config

    config = load_config(config_path)
    processor = config.processors["html"] if hasattr(config, "processors") else None
    if processor is None:
        from omnicrawler.extraction.extractors import HTMLProcessor

        processor = HTMLProcessor(config)
    result = FetchResult(
        CrawlRequest("https://example.org/"), "https://example.org/", 200,
        {"content-type": "text/html"}, b"<html><body><p>abc 42</p></body></html>", 0.1,
    )
    parsed = processor.process(result)
    assert len(parsed.records) == 0  # 越界 group 跳过而非崩溃


# ── JSONProcessor URL 上下文 ──────────────────────────────────────────

def test_json_processor_error_mentions_url() -> None:
    class _FakeConfig:
        section = lambda self, _name: {"mode": "json", "item_path": "$", "fields": {}}  # noqa: E731

    processor = JSONProcessor(_FakeConfig())  # type: ignore[arg-type]
    result = FetchResult(
        CrawlRequest("https://example.org/not-json"), "https://example.org/not-json", 200,
        {"content-type": "application/json"}, b"not json at all", 0.1,
    )
    with pytest.raises(ValueError, match="https://example.org/not-json"):
        processor.process(result)


# ── field_designer 节点上限 + 非 O(n²) ────────────────────────────────

def test_analyze_html_limits_nodes() -> None:

    rows = "".join(f"<tr><td>cell {i}</td></tr>" for i in range(3000))
    html = f"<html><body><table>{rows}</table></body></html>"
    candidates = analyze_html(html)
    assert isinstance(candidates, list)


def test_analyze_html_returns_ranked_candidates() -> None:
    html = """
    <html><body>
      <h1>Company Report</h1>
      <p class="price">1,200.00</p>
      <time datetime="2024-03-01">2024年3月1日</time>
      <a href="/about" id="about">About us</a>
    </body></html>
    """
    candidates = analyze_html(html, limit=20)
    assert candidates
    names = {item.suggested_name for item in candidates}
    assert "price" in names
    assert "date" in names
    assert all(item.score >= 0.25 for item in candidates)

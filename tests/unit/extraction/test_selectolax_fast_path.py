"""Tests for selectolax Lexbor fast-path and CapabilitySlot('html_parser') routing."""

from __future__ import annotations

import pytest

from omnicrawler.core.capabilities import CapabilityProvider, default_capability_slots
from omnicrawler.extraction import intelligent_scraper
from omnicrawler.extraction.intelligent_scraper import (
    DOMNode,
    _parse_dom,
    detect_repeating_patterns,
    infer_fields,
)

SAMPLE_HTML = """<!doctype html>
<html>
<head><title>Book Store</title><script>var x = 1;</script></head>
<body>
  <div class="catalog" id="main-list">
    <article class="book-card featured" itemprop="itemListElement">
      <h2 class="title"><a href="/b/1" title="Book One">Deep Crawling Systems</a></h2>
      <span class="price">¥89.00</span>
      <span class="date">2026-11-01</span>
    </article>
    <article class="book-card" itemprop="itemListElement">
      <h2 class="title"><a href="/b/2" title="Book Two">Structured Data Pipelines</a></h2>
      <span class="price">¥99.00</span>
      <span class="date">2026-11-05</span>
    </article>
    <article class="book-card" itemprop="itemListElement">
      <h2 class="title"><a href="/b/3" title="Book Three">Resilient Desktop Agents</a></h2>
      <span class="price">¥79.00</span>
      <span class="date">2026-11-10</span>
    </article>
  </div>
</body>
</html>
"""


def test_selectolax_and_lxml_produce_equivalent_dom_features_and_fields() -> None:
    pytest.importorskip("selectolax")
    lxml_nodes = _parse_dom(SAMPLE_HTML, parser="lxml")
    lexbor_nodes = _parse_dom(SAMPLE_HTML, parser="selectolax")

    assert len(lexbor_nodes) == len(lxml_nodes) > 5
    for lx, sx in zip(lxml_nodes, lexbor_nodes, strict=True):
        assert sx.tag == lx.tag
        assert sx.classes == lx.classes
        assert sx.id_ == lx.id_
        assert sx.css_path == lx.css_path
        assert sx.href == lx.href
        assert sx.itemprop == lx.itemprop
        assert sx.depth == lx.depth
        assert sx.children_keys == lx.children_keys

    # Pattern detection & field inference yield identical extraction rules
    p_lxml = detect_repeating_patterns(lxml_nodes)
    p_lex = detect_repeating_patterns(lexbor_nodes)
    assert p_lxml and p_lex
    assert p_lex[0].css_path == p_lxml[0].css_path
    assert infer_fields(p_lex, lexbor_nodes) == infer_fields(p_lxml, lxml_nodes)


def test_html_parser_slot_plugin_provider_can_override_dom_parser() -> None:
    slot = default_capability_slots().slot("html_parser")
    assert slot is not None
    sentinel = [DOMNode(tag="div", classes=["from-plugin"], text="ok", css_path="div.from-plugin")]
    slot.register(
        CapabilityProvider(
            provider_id="mock-plugin-parser",
            slot_name="html_parser",
            source="plugin",
            display_name="Mock Plugin Parser",
            priority=50,
            factory=lambda html, max_nodes=5000: list(sentinel),
        )
    )
    try:
        out = intelligent_scraper._parse_dom("<html><body></body></html>", parser="mock-plugin-parser")
        assert out == sentinel
    finally:
        slot.unregister("mock-plugin-parser")

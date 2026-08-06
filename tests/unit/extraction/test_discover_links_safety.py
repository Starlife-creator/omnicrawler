"""S2.5.34：discover_links 去重 + 伪协议过滤。"""

from __future__ import annotations

from omnicrawl.extraction.html_tools import discover_links, parse_html


def test_pseudo_protocols_are_filtered() -> None:
    html = b"""
    <html><body>
      <a href="javascript:void(0)">Bad JS</a>
      <a href="JAVASCRIPT:alert(1)">Bad alert</a>
      <a href="mailto:test@example.org">Mail</a>
      <a href="tel:+8612345">Phone</a>
      <a href="data:text/html,hi">Data</a>
      <a href="https://example.org/ok">Good</a>
      <a href="/relative">Relative</a>
    </body></html>
    """
    links = discover_links(parse_html(html))
    urls = [item[0] for item in links]
    assert "https://example.org/ok" in urls
    assert "/relative" in urls
    assert not any(
        url.casefold().startswith(("javascript:", "mailto:", "tel:", "data:"))
        for url in urls
    )
    assert not any("void" in url.casefold() for url in urls)


def test_duplicate_links_are_deduped() -> None:
    html = b"""
    <html><body>
      <a href="https://example.org/a">First</a>
      <a href="https://example.org/a">Second</a>
      <a href="https://example.org/a">Third</a>
      <a href="https://example.org/b">Unique</a>
    </body></html>
    """
    links = discover_links(parse_html(html))
    urls = [item[0] for item in links]
    assert urls.count("https://example.org/a") == 1
    assert urls.count("https://example.org/b") == 1

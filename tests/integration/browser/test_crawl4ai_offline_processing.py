import socket

import pytest


def test_real_crawl4ai_extracts_and_generates_markdown_without_network(monkeypatch):
    pytest.importorskip("crawl4ai")
    from omnicrawler.sources.crawl4ai_bridge import C4AConfig, Crawl4AIEngine
    def reject_network(*args, **kwargs):
        pytest.fail("offline dependency processing attempted networking")
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    config = C4AConfig(extraction_strategy="css", extraction_schema={
        "name": "items", "baseSelector": "article",
        "fields": [{"name": "title", "selector": "h2", "type": "text"}],
    })
    html = '<html><head><title>Offline</title></head><body><article><h2>Contract A</h2></article><img src="https://example.org/image.jpg"></body></html>'
    result = Crawl4AIEngine(config).process_html(html, "https://example.org/page")
    assert result.extracted["records"] == [{"title": "Contract A"}]
    assert "Contract A" in result.markdown
    assert result.title == "Offline"

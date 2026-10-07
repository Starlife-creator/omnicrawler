from types import SimpleNamespace

import pytest

from omnicrawler.sources.crawl4ai_bridge import C4AConfig, Crawl4AIEngine


def test_failed_raw_result_cannot_become_http_success():
    result = Crawl4AIEngine()._convert(SimpleNamespace(url="https://example.org/", status_code=0))
    assert result.status == 0


def test_bridge_renders_through_owned_native_fetcher(monkeypatch):
    engine = Crawl4AIEngine()
    captured = []
    class Fetcher:
        def __init__(self, config, *, egress):
            captured.append((config.section("browser")["engine"], egress))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            captured.append("closed")
        def fetch(self, request):
            return SimpleNamespace(body=b"<h1>Native</h1>", final_url=request.url, status=503)
    monkeypatch.setattr("omnicrawler.sources.crawl4ai_bridge.BrowserFetcher", Fetcher)
    monkeypatch.setattr(engine, "process_html", lambda html, url, **options: SimpleNamespace(**options, metadata={}))
    result = engine._fetch_guarded("https://example.org/", C4AConfig())
    assert result.status == 503 and result.metadata["network_contract"] == "native_guarded"
    assert captured[0][0] == "playwright" and captured[-1] == "closed"


def test_native_modes_reject_third_party_autonomous_networking():
    for config in (C4AConfig(virtual_scroll=True), C4AConfig(adaptive=True), C4AConfig(extraction_strategy="llm")):
        with pytest.raises(ValueError):
            Crawl4AIEngine._validate_modes(config)

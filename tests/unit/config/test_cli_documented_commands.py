import asyncio
import sys

import pytest

from omnicrawl import cli
from omnicrawl.core.errors import PolicyBlockedError
from omnicrawl.sources.crawl4ai_bridge import C4AConfig, Crawl4AIEngine


def test_crawl4ai_documented_command_is_registered_and_forwarded(monkeypatch):
    received = []
    monkeypatch.setattr("omnicrawl.crawl4ai_bridge.main", lambda: received.append(sys.argv[1:]))

    cli.main(["c4a-fetch", "https://example.test", "--stealth", "--extract", "schema.json", "-o", "result.json"])

    assert received == [["https://example.test", "--stealth", "--extract", "schema.json", "--output", "result.json"]]


def test_stealth_json_and_template_generate_flags_are_forwarded(monkeypatch):
    stealth = []
    templates = []
    monkeypatch.setattr("omnicrawl.stealth_enhanced.main", lambda: stealth.append(sys.argv[1:]))
    monkeypatch.setattr("omnicrawl.apify_templates.main", lambda: templates.append(sys.argv[1:]))

    cli.main(["stealth-fingerprint", "--count", "3", "--json"])
    cli.main(["gen-templates", "--generate", "amazon"])

    assert stealth == [["--count", "3", "--json"]]
    assert templates == [["--generate", "amazon"]]


def test_crawl4ai_bridge_blocks_private_targets_and_plaintext_url_credentials():
    engine = Crawl4AIEngine()

    with pytest.raises(PolicyBlockedError):
        engine.fetch("http://127.0.0.1/private")
    with pytest.raises(ValueError, match="明文凭据"):
        engine.fetch("https://user:password@example.test/")


def test_crawl4ai_config_rejects_invalid_runtime_bounds():
    with pytest.raises(ValueError, match="timeout_ms"):
        C4AConfig(timeout_ms=0)
    with pytest.raises(ValueError, match="browser_type"):
        C4AConfig(browser_type="unknown")


def test_crawl4ai_sync_fetch_preserves_real_errors_with_and_without_running_loop(monkeypatch):
    engine = Crawl4AIEngine(C4AConfig(allow_private_network=True))
    engine._available = True

    async def fail(_url, _config):
        raise RuntimeError("render failed")

    monkeypatch.setattr(engine, "_fetch_async", fail)
    direct = engine.fetch("http://127.0.0.1/page")

    async def call_from_loop():
        return engine.fetch("http://127.0.0.1/page")

    nested = asyncio.run(call_from_loop())
    assert direct.error == "RuntimeError: render failed"
    assert nested.error == "RuntimeError: render failed"

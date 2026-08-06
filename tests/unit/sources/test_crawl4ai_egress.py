"""S2.5.5：crawl4ai 走 EgressBroker + 指纹含 headers + metadata/status 防护。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest
from omnicrawl.security.egress import EgressBroker
from omnicrawl.sources.crawl4ai_bridge import C4AConfig, C4AResult, Crawl4AIEngine

# ── 指纹含 headers ────────────────────────────────────────────────────

def test_fingerprint_differs_by_headers() -> None:
    base = CrawlRequest("https://example.org/")
    zh = CrawlRequest("https://example.org/", headers={"Accept-Language": "zh-CN"})
    en = CrawlRequest("https://example.org/", headers={"Accept-Language": "en-US"})
    assert base.fingerprint != zh.fingerprint
    assert zh.fingerprint != en.fingerprint


def test_fingerprint_is_order_independent() -> None:
    a = CrawlRequest("https://example.org/", headers={"X-A": "1", "X-B": "2"})
    b = CrawlRequest("https://example.org/", headers={"X-B": "2", "X-A": "1"})
    assert a.fingerprint == b.fingerprint


# ── metadata None 防护 / status 真实透传 ───────────────────────────────

class _Raw:
    def __init__(self, *, metadata=None, status_code=200, url="https://example.org/"):
        self.url = url
        self.metadata = metadata
        self.status_code = status_code
        self.markdown = "md"
        self.html = "<html></html>"
        self.text = "text"
        self.extracted_content = {}
        self.links = []
        self.media = []
        self.tables = []
        self.screenshot = None


def test_convert_metadata_none_does_not_crash() -> None:
    engine = Crawl4AIEngine()
    result = engine._convert(_Raw(metadata=None))
    assert result.title == ""
    assert result.metadata == {}


def test_convert_status_code_passthrough() -> None:
    engine = Crawl4AIEngine()
    assert engine._convert(_Raw(status_code=404)).status == 404
    assert engine._convert(_Raw(status_code=403)).status == 403
    assert engine._convert(_Raw(status_code=0)).status == 200
    assert engine._convert(_Raw(status_code=None)).status == 200


# ── EgressBroker 接入 ─────────────────────────────────────────────────

def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s255, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "egress: {enabled: true, audit: true}\n",
        encoding="utf-8",
    )
    return config_path


def test_engine_authorizes_through_broker(tmp_path: Path) -> None:
    broker = EgressBroker(load_config(_config(tmp_path)))
    engine = Crawl4AIEngine(egress=broker)
    before = broker.snapshot()
    engine._authorize("https://example.org/page", C4AConfig())
    after = broker.snapshot()
    assert after.requests == before.requests + 1
    assert broker.audit_status()["write_failures"] == 0


def test_engine_budget_enforced_by_broker(tmp_path: Path) -> None:
    from omnicrawl.core.errors import EgressBudgetExceededError

    config_path = _config(tmp_path)
    config = load_config(config_path)
    config.raw["egress"]["maximum_requests"] = 1
    broker = EgressBroker(config)
    engine = Crawl4AIEngine(egress=broker)
    engine._authorize("https://example.org/a", C4AConfig())
    with pytest.raises(EgressBudgetExceededError):
        engine._authorize("https://example.org/b", C4AConfig())


def test_record_result_accounts_audit_and_success(tmp_path: Path) -> None:
    broker = EgressBroker(load_config(_config(tmp_path)))
    engine = Crawl4AIEngine(egress=broker)
    ok = C4AResult(url="https://example.org/", html="<h1>hi</h1>", markdown="hi")
    engine._record_result("https://example.org/", ok)
    snap = broker.snapshot()
    assert snap.response_bytes == len("<h1>hi</h1>") + len("hi")
    fail = C4AResult(url="https://example.org/bad", status=0, error="boom")
    engine._record_result("https://example.org/bad", fail)
    assert broker.snapshot().requests == snap.requests


def test_engine_without_broker_still_guards_credentials(tmp_path: Path) -> None:
    engine = Crawl4AIEngine()
    with pytest.raises(ValueError, match="明文凭据"):
        engine._authorize("https://user:secret@example.org/", C4AConfig())

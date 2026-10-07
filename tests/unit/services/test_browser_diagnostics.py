from types import SimpleNamespace

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.services.browser_diagnostics import fallback_policy, probe


def _config(tmp_path):
    path = tmp_path / "browser.yaml"
    path.write_text("project: {name: probe, workspace: work}\nsource: {kind: browser, seeds: [https://example.org]}\n", encoding="utf8")
    return load_config(path)


@pytest.mark.parametrize("section,key", [("session", "persist_cookies"), ("browser", "persist_profile"), ("source", "auth_check")])
def test_account_conditions_block_automatic_engine_switch(tmp_path, section, key):
    config = _config(tmp_path)
    config.raw["browser"]["selenium_fallback_engine"] = "playwright"
    assert fallback_policy(config)["allowed"]
    config.raw[section][key] = True
    assert not fallback_policy(config)["allowed"]


def test_probe_isolates_accounts_actions_and_closes_renderer(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.raw["session"]["persist_cookies"] = True
    config.raw["browser"]["actions"] = [{"action": "fill", "value": "PRIVATE-VALUE"}]
    config.raw["http"]["headers"] = {"Authorization": "PRIVATE-TOKEN"}
    owned = []
    class Renderer:
        def __init__(self, isolated):
            owned.append(self)
            self.config, self.closed = isolated, False
        def fetch(self, request):
            assert self.config.workspace != config.workspace
            assert not self.config.raw["session"]["persist_cookies"]
            assert self.config.raw["browser"]["actions"] == []
            assert self.config.raw["http"]["headers"] == {}
            assert request.url.startswith("http://127.0.0.1:")
            return SimpleNamespace(body=b"omnicrawler-owned-renderer-probe")
        def close(self):
            self.closed = True
    monkeypatch.setattr("omnicrawler.fetching.browser_fetcher.BrowserFetcher", Renderer)
    result = probe(config)
    assert result["status"] == "passed" and owned[0].closed
    assert not result["fallback"]["allowed"]
    assert "PRIVATE" not in str(result)
    assert not owned[0].config.workspace.exists()

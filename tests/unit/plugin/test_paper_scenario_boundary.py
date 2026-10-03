from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_loader import load_local_plugins
from omnicrawler.plugins.plugin_registry import Registry
from tools.audit_paper_scenario import audit


def test_selected_market_paper_plugin_rejected_before_execution():
    root = Path(__file__).resolve().parents[3]
    directory = root.parent / "OmniCrawler-market/plugins/academic-paper-downloader"
    assert (directory / "plugin.py").is_file(), "pinned market checkout is required; no empty acceptance"
    report = audit(directory)
    assert report["decision"] == "stop_plugin_route"
    assert "httpx" in report["direct_network_modules"] and not report["executed_plugin"]
    registry = Registry()
    permissions = ("network:scoped", "records:read", "state:read", "state:write", "secrets:read", "files:read")
    with pytest.raises(PermissionError, match="不得直接导入网络客户端"):
        load_local_plugins(registry, [str(directory / "plugin.py")], directory,
            approved_permissions=permissions, signature_policy="developer")
    assert registry.plugins == []

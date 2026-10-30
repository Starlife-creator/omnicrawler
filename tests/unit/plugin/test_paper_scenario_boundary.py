from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_loader import load_local_plugins
from omnicrawler.plugins.plugin_registry import Registry
from tools.audit_paper_scenario import audit, resolve_payload


def test_selected_market_paper_plugin_rejected_before_execution():
    root = Path(__file__).resolve().parents[3]
    directory = root.parent / "OmniCrawler-market/plugins/academic-paper-downloader"
    plugin_file, _manifest_file = resolve_payload(directory)
    assert plugin_file.is_file(), "pinned market checkout is required; no empty acceptance"
    report = audit(directory)
    # 审计对象必须是 market.yaml 指向的当前版本载荷，不能退回根目录那份遗留副本
    assert report["source_file"].endswith(f"versions/{report['manifest_version']}/plugin.py"), (
        f"审计到了非当前版本载荷: {report['source_file']}（版本 {report['manifest_version']}）"
    )
    assert report["decision"] == "stop_plugin_route"
    assert "httpx" in report["direct_network_modules"] and not report["executed_plugin"]
    registry = Registry()
    permissions = ("network:scoped", "records:read", "state:read", "state:write", "secrets:read", "files:read")
    with pytest.raises(PermissionError, match="不得直接导入网络客户端"):
        load_local_plugins(registry, [str(plugin_file)], plugin_file.parent,
            approved_permissions=permissions, signature_policy="developer")
    assert registry.plugins == []


def test_resolve_payload_prefers_market_yaml_pointer(tmp_path: Path) -> None:
    """根目录留着早期扁平载荷时，必须按 market.yaml 解析到 versions/<v>/。"""
    plugin_dir = tmp_path / "plugins" / "demo"
    version_dir = plugin_dir / "versions" / "2.0.0"
    version_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text("# legacy\n", encoding="utf-8")
    (plugin_dir / "package.manifest.json").write_text('{"version": "0.1.0"}', encoding="utf-8")
    (version_dir / "plugin.py").write_text("# current\n", encoding="utf-8")
    (version_dir / "package.manifest.json").write_text('{"version": "2.0.0"}', encoding="utf-8")
    (plugin_dir / "market.yaml").write_text(
        "version: 2.0.0\n"
        "plugin_file: plugins/demo/versions/2.0.0/plugin.py\n"
        "package_manifest_file: plugins/demo/versions/2.0.0/package.manifest.json\n",
        encoding="utf-8",
    )
    plugin_path, manifest_path = resolve_payload(plugin_dir)
    assert plugin_path == version_dir / "plugin.py"
    assert manifest_path == version_dir / "package.manifest.json"


def test_resolve_payload_falls_back_to_flat_layout(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text("# flat\n", encoding="utf-8")
    (plugin_dir / "package.manifest.json").write_text('{"version": "0.1.0"}', encoding="utf-8")
    plugin_path, manifest_path = resolve_payload(plugin_dir)
    assert plugin_path == plugin_dir / "plugin.py"
    assert manifest_path == plugin_dir / "package.manifest.json"

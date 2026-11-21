from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_loader import load_local_plugins
from omnicrawler.plugins.plugin_registry import Registry
from tools.qa.audit_paper_scenario import audit, resolve_payload


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
    # **判据随插件修复而演进**：0.9.0 起重构为「所有网络经宿主 EgressBroker」
    # （见插件 `_fetch` 的注释），生产代码不再直接 import httpx ——
    # 审计结论于是从 `stop_plugin_route`（直接联网，必须停止）
    # 变为 `requires_runtime_acceptance`（无直接联网，但需运行时验收）。
    #
    # 这不是放宽：`direct_network_modules` 为空是**更强**的结论，
    # 说明该插件已经不能绕过 EgressBroker。判据钉住这两点：
    #   1. 决策不再是「停止」（因为它已经合规）；
    #   2. 确实没有直接联网模块（否则仍应停止）。
    assert report["decision"] == "requires_runtime_acceptance", (
        f"决策为 {report['decision']!r}：若 direct_network_modules 非空，"
        "应当回到 stop_plugin_route"
    )
    assert not report["direct_network_modules"], (
        f"仍存在直接联网模块 {report['direct_network_modules']} —— "
        "那正是必须阻断的理由"
    )
    assert not report["executed_plugin"], "审计必须是静态的，绝不能执行插件"
    registry = Registry()
    # 0.9.0 起 PDF 经 `artifact.stream` 交付，故新增 `artifacts:write`。
    # 这里**故意只批一部分权限**，验证「未批准的权限必须挡住加载」——
    # 这是插件权限模型的核心判据，与它是否直接联网无关。
    permissions = (
        "network:scoped",
        "records:read",
        "state:read",
        "state:write",
        "secrets:read",
        "files:read",
    )
    with pytest.raises(PermissionError, match="permissions were not approved"):
        load_local_plugins(
            registry,
            [str(plugin_file)],
            plugin_file.parent,
            approved_permissions=permissions,
            signature_policy="developer",
        )
    assert registry.plugins == [], "权限未批准时不得注册任何插件"


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

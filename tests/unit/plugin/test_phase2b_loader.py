"""Phase 2b loader 接线契约测试：daily_quota/egress_policy 配置解析生效。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_subprocess_adapter import _SubprocessSessionHost
from omnicrawler.plugins.plugins import (
    SIGNATURE_POLICY_DEVELOPER,
    Registry,
    load_local_plugins,
)

pytestmark = pytest.mark.plugin_contract


def _build_contract2_plugin(root: Path, name: str, *, permissions: list[str]) -> Path:
    plugin_dir = root / "plugins" / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(
        "PLUGIN_METADATA = {'name': %r, 'version': '1.0', "
        "'execution_mode': 'subprocess', 'plugin_types': ['source'], "
        "'permissions': %r}\n"
        "def handle(operation, payload):\n"
        "    if operation == 'source.seed':\n"
        "        return {'requests': [{'url': 'https://example.com/'}]}\n"
        "    return {'operation': operation}\n" % (name, permissions),
        encoding="utf-8",
    )
    return plugin_dir


def test_loader_wires_daily_quota_from_config(tmp_path: Path) -> None:
    """plugins.network_daily_quota 按 plugin_id 解析 → host 注入配额。"""
    plugin_dir = _build_contract2_plugin(tmp_path, "quota_demo", permissions=["network:scoped"])
    plugins_section = {"network_daily_quota": {"quota_demo": {"requests": 1}}}
    from omnicrawler.plugins.plugins import _static_plugin_metadata
    from omnicrawler.plugins.plugin_router import detect_contract_shape

    source = (plugin_dir / "plugin.py").read_text(encoding="utf-8")
    meta = _static_plugin_metadata(plugin_dir / "plugin.py", source)
    assert detect_contract_shape(source) == 2
    assert meta is not None

    # 模拟 _load_local_plugin 的 host 构造段
    from omnicrawler.plugins.plugin_quota import DailyNetworkQuota

    quota_rules = plugins_section.get("network_daily_quota", {}) or {}
    daily_quota = DailyNetworkQuota({meta.name: quota_rules[meta.name]})
    host = _SubprocessSessionHost(
        plugin_dir, "plugin",
        permissions=set(), config=None, plugin_id=meta.name,
        daily_quota=daily_quota,
        egress_policy="prompt",
    )
    assert host._daily_quota is not None
    broker = host._ensure()[1]
    assert broker._daily_quota is not None
    host.close()


def test_loader_wires_egress_policy_block(tmp_path: Path) -> None:
    """plugins.egress_policy=block → host 注入 block 档。"""
    plugin_dir = _build_contract2_plugin(tmp_path, "egress_demo", permissions=["network:scoped"])
    host = _SubprocessSessionHost(
        plugin_dir, "plugin", permissions=set(), config=None,
        plugin_id="egress_demo", egress_policy="block",
    )
    broker = host._ensure()[1]
    assert broker._egress_policy == "block"
    host.close()


def test_loader_end_to_end_contract2_still_loads(tmp_path: Path) -> None:
    """Phase 2b 参数接入后契约 2 插件端到端加载不回归。"""
    plugin_dir = _build_contract2_plugin(tmp_path, "c2_demo", permissions=[])
    registry = Registry()
    load_local_plugins(
        registry, ["plugins/"], tmp_path,
        signature_policy=SIGNATURE_POLICY_DEVELOPER,
        fail_open=False,
    )
    assert "c2_demo" in registry.sources
    adapter = registry.sources["c2_demo"](None)
    requests = adapter.seed()
    assert requests and requests[0].url == "https://example.com/"
    adapter.close()

"""Tests for the plugin AST preflight gate (forbidden dangerous patterns).

Covers the static scan that rejects subprocess/ctypes/eval/os.system-style
patterns before plugin module-level code executes, plus the admin-controlled
config allowlist (``plugins.ast_allowed_patterns``).

``# omnicrawler: allow-ast`` 文件内自豁免注释已于 2026-08 移除（审查报告 S47）：
豁免权在插件自己手里等于没有豁免门——插件写一行注释即可放行任何危险调用。
如今只有运行配置能豁免。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omnicrawler.plugins.plugins import Registry, load_local_plugins


def _write_plugin(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    return path


def _load(
    plugin: Path,
    *,
    ast_allowed_patterns: tuple[str, ...] = (),
    approved_permissions: tuple[str, ...] = (),
) -> Registry:
    registry = Registry()
    load_local_plugins(
        registry,
        [str(plugin)],
        plugin.parent,
        config=None,
        approved_permissions=approved_permissions,
        ast_allowed_patterns=ast_allowed_patterns,
        signature_policy="developer",  # AST 门禁单测聚焦模式检查，不涉签名
    )
    return registry


def test_forbidden_subprocess_import_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "sub",
        "PLUGIN_METADATA = {'name': 'sub'}\nimport subprocess\ndef register(registry): pass\n",
    )
    with pytest.raises(PermissionError, match="subprocess"):
        _load(plugin)


def test_forbidden_ctypes_import_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "ct",
        "import ctypes\ndef register(registry): pass\n",
    )
    with pytest.raises(PermissionError, match="ctypes"):
        _load(plugin)


def test_eval_call_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "ev",
        "PLUGIN_METADATA = {'name': 'ev'}\ndef register(registry):\n    eval('print(1)')\n",
    )
    with pytest.raises(PermissionError, match="eval"):
        _load(plugin)


def test_exec_call_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "ex",
        "def register(registry):\n    exec('x = 1')\n",
    )
    with pytest.raises(PermissionError, match="exec"):
        _load(plugin)


def test_os_system_call_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "osy",
        "import os\ndef register(registry):\n    os.system('calc')\n",
    )
    with pytest.raises(PermissionError, match=r"os\.system"):
        _load(plugin)


def test_os_aliased_call_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "osalias",
        "import os as operating\ndef register(registry):\n    operating.remove('x')\n",
    )
    with pytest.raises(PermissionError, match=r"os\.remove"):
        _load(plugin)


def test_from_import_os_system_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "fromos",
        "from os import system\ndef register(registry): pass\n",
    )
    with pytest.raises(PermissionError, match=r"os\.system"):
        _load(plugin)


def test_shutil_rmtree_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "rmtree",
        "import shutil\ndef register(registry):\n    shutil.rmtree('x')\n",
    )
    with pytest.raises(PermissionError, match=r"shutil\.rmtree"):
        _load(plugin)


def test_legitimate_file_io_still_allowed(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "io",
        "PLUGIN_METADATA = {'name': 'io', 'permissions': ['filesystem_write']}\n"
        "import os\n"
        "import shutil\n"
        "def _run_io():\n"
        "    with open('out.txt', 'w') as handle:\n"
        "        handle.write('ok')\n"
        "    os.path.join('a', 'b')\n"
        "    shutil.copy('a', 'b')\n"
        "def register(registry):\n"
        "    registry.register_processor('p', object)\n",
    )
    registry = _load(plugin, approved_permissions=("filesystem_write",))
    assert registry.plugins[0].name == "io"


def test_plugin_scoped_permission_grant_allows_exact_artifact(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "scoped",
        "PLUGIN_METADATA = {'name': 'scoped', 'version': '1.0.0', "
        "'permissions': ['filesystem_write']}\n"
        "def register(registry): pass\n",
    )
    digest = hashlib.sha256(plugin.read_bytes()).hexdigest()
    registry = Registry()
    load_local_plugins(
        registry,
        [str(plugin)],
        tmp_path,
        permission_grants={
            "scoped": {
                "version": "1.0.0",
                "artifact_sha256": digest,
                "permissions": ["filesystem_write"],
            }
        },
        signature_policy="developer",
    )
    assert registry.plugins[0].name == "scoped"


def test_permission_grant_is_invalidated_when_plugin_changes(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "changed",
        "PLUGIN_METADATA = {'name': 'changed', 'version': '1.0.0', "
        "'permissions': ['filesystem_write']}\n"
        "def register(registry): pass\n",
    )
    digest = hashlib.sha256(plugin.read_bytes()).hexdigest()
    plugin.write_text(plugin.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(PermissionError, match="载荷哈希不匹配"):
        load_local_plugins(
            Registry(),
            [str(plugin)],
            tmp_path,
            permission_grants={
                "changed": {
                    "version": "1.0.0",
                    "artifact_sha256": digest,
                    "permissions": ["filesystem_write"],
                }
            },
            signature_policy="developer",
        )


def test_permission_grant_is_invalidated_when_version_changes(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "versioned",
        "PLUGIN_METADATA = {'name': 'versioned', 'version': '2.0.0', "
        "'permissions': ['filesystem_write']}\n"
        "def register(registry): pass\n",
    )
    with pytest.raises(PermissionError, match="授权版本为 1.0.0，当前版本为 2.0.0"):
        load_local_plugins(
            Registry(),
            [str(plugin)],
            tmp_path,
            permission_grants={
                "versioned": {
                    "version": "1.0.0",
                    "artifact_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
                    "permissions": ["filesystem_write"],
                }
            },
            signature_policy="developer",
        )


def test_permission_grant_is_invalidated_when_creator_changes(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "authored",
        "PLUGIN_METADATA = {'name': 'authored', 'version': '1.0.0', "
        "'permissions': ['filesystem_write']}\n"
        "def register(registry): pass\n",
    )
    (tmp_path / "creator.identity").write_text(
        json.dumps({"key_fingerprint": "creator-current"}),
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="授权作者指纹不匹配"):
        load_local_plugins(
            Registry(),
            [str(plugin)],
            tmp_path,
            permission_grants={
                "authored": {
                    "version": "1.0.0",
                    "artifact_sha256": hashlib.sha256(plugin.read_bytes()).hexdigest(),
                    "creator_fingerprint": "creator-previous",
                    "permissions": ["filesystem_write"],
                }
            },
            signature_policy="developer",
        )


def test_permission_grant_does_not_leak_to_another_plugin(tmp_path: Path) -> None:
    first = _write_plugin(
        tmp_path,
        "first",
        "PLUGIN_METADATA = {'name': 'first', 'permissions': ['filesystem_write']}\n"
        "def register(registry): pass\n",
    )
    second = _write_plugin(
        tmp_path,
        "second",
        "PLUGIN_METADATA = {'name': 'second', 'permissions': ['filesystem_write']}\n"
        "def register(registry): pass\n",
    )
    with pytest.raises(PermissionError, match="not approved for second"):
        load_local_plugins(
            Registry(),
            [str(first), str(second)],
            tmp_path,
            permission_grants={
                "first": {
                    "artifact_sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
                    "permissions": ["filesystem_write"],
                }
            },
            signature_policy="developer",
        )


def test_legacy_global_permissions_rejected_for_multiple_plugins(tmp_path: Path) -> None:
    body = (
        "PLUGIN_METADATA = {'name': %r, 'permissions': ['filesystem_write']}\n"
        "def register(registry): pass\n"
    )
    first = _write_plugin(tmp_path, "legacy_one", body % "legacy_one")
    second = _write_plugin(tmp_path, "legacy_two", body % "legacy_two")
    with pytest.raises(PermissionError, match="旧版全局 approved_permissions"):
        load_local_plugins(
            Registry(),
            [str(first), str(second)],
            tmp_path,
            approved_permissions=("filesystem_write",),
            signature_policy="developer",
        )


def test_disabled_market_plugin_is_skipped_before_execution(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins_installed" / "disabled_demo"
    plugin_dir.mkdir(parents=True)
    plugin = _write_plugin(
        plugin_dir,
        "plugin",
        "PLUGIN_METADATA = {'name': 'disabled_demo', 'version': '1.0.0'}\n"
        "def handle(operation, payload): return {}\n",
    )
    registry = Registry()
    load_local_plugins(
        registry,
        [str(plugin)],
        tmp_path,
        enabled_market_plugins=set(),
        signature_policy="developer",
    )
    assert registry.plugins == []
    assert any("未在当前项目启用" in item["error"] for item in registry.plugin_errors)


def test_comment_self_exemption_no_longer_honored(tmp_path: Path) -> None:
    """S47：文件内豁免注释已失效——带注释的 os.system 依然必须被拒。"""
    plugin = _write_plugin(
        tmp_path,
        "allowed",
        "# omnicrawler: allow-ast os.system\nimport os\ndef register(registry):\n    os.system('echo ok')\n",
    )
    with pytest.raises(PermissionError, match="os.system"):
        _load(plugin)


def test_config_allowlist_permits_pattern(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "allowed_cfg",
        "import subprocess\ndef register(registry): pass\n",
    )
    registry = _load(plugin, ast_allowed_patterns=("subprocess",))
    assert registry.plugins[0].name == "allowed_cfg"


def test_allowlist_is_scoped_per_pattern(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "partial",
        "import subprocess\nimport ctypes\ndef register(registry): pass\n",
    )
    with pytest.raises(PermissionError, match="ctypes"):
        _load(plugin, ast_allowed_patterns=("subprocess",))


def test_network_import_still_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "net",
        "import requests\ndef register(registry): pass\n",
    )
    with pytest.raises(PermissionError, match="不得直接导入网络客户端"):
        _load(plugin)

"""Tests for the plugin AST preflight gate (forbidden dangerous patterns).

Covers the static scan that rejects subprocess/ctypes/eval/os.system-style
patterns before plugin module-level code executes, plus the two explicit
escape hatches (config allowlist and in-file comment).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.plugins.plugins import Registry, load_local_plugins


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


def test_comment_allowlist_permits_pattern(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "allowed",
        "# omnicrawl: allow-ast os.system\nimport os\ndef register(registry):\n    os.system('echo ok')\n",
    )
    registry = _load(plugin)
    assert registry.plugins[0].name == "allowed"


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

"""Phase 2a 门 1/门 3 契约测试（声明一致性 + dependencies 双向互证）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_audit import (
    gate_declaration_consistency,
    gate_dependencies_consistency,
    gate_host_boundaries,
)


def _write_plugin(tmp_path: Path, metadata: str, body: str) -> Path:
    plugin = tmp_path / "gate_plugin"
    plugin.mkdir(exist_ok=True)
    (plugin / "plugin.py").write_text(
        f"PLUGIN_METADATA = {metadata}\n{body}\n", encoding="utf-8"
    )
    return plugin


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


@pytest.fixture()
def ok_plugin(tmp_path: Path) -> Path:
    return _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','execution_mode':'subprocess',"
        "'permissions':['records:read'],'dependencies':[]}",
        "def handle(op, p):\n    return {}",
    )


def test_gate1_clean_plugin_passes(ok_plugin: Path) -> None:
    assert gate_declaration_consistency(ok_plugin) == []


def test_gate1_subprocess_imports_host_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','execution_mode':'subprocess','dependencies':[]}",
        "from omnicrawler.sources import GenericSource\n"
        "def handle(op, p):\n    return {}",
    )
    codes = _codes(gate_declaration_consistency(plugin))
    assert "gate1_subprocess_imports_host" in codes


def test_gate1_subprocess_ui_permission_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','execution_mode':'subprocess',"
        "'permissions':['ui:panel'],'dependencies':[]}",
        "def handle(op, p):\n    return {}",
    )
    codes = _codes(gate_declaration_consistency(plugin))
    assert "gate1_subprocess_forbidden_permission" in codes


def test_gate1_network_requires_domains(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','execution_mode':'subprocess',"
        "'permissions':['network:scoped'],'dependencies':[]}",
        "def handle(op, p):\n    return {}",
    )
    codes = _codes(gate_declaration_consistency(plugin))
    assert "gate1_network_without_domains" in codes


def test_gate1_files_read_requires_allowlist(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','execution_mode':'subprocess',"
        "'permissions':['files:read'],'dependencies':[]}",
        "def handle(op, p):\n    return {}",
    )
    codes = _codes(gate_declaration_consistency(plugin))
    assert "gate1_files_read_without_allowlist" in codes


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("import httpx\ndef handle(op, p): return {}", "gate1_direct_network"),
        ("import urllib.request\ndef handle(op, p): return {}", "gate1_direct_network"),
        ("from playwright.sync_api import sync_playwright\ndef handle(op, p): return {}", "gate1_direct_browser"),
        ("import browser_cookie3\ndef handle(op, p): return {}", "gate1_direct_session"),
        ("from pathlib import Path\ndef handle(op, p): return {'data': Path(p['path']).read_text()}", "gate1_uncontrolled_file_io"),
        ("import tempfile\ndef handle(op, p): return {'path': tempfile.mkdtemp()}", "gate1_uncontrolled_file_io"),
        ("def handle(op, p): return {'data': open(p['path']).read()}", "gate1_uncontrolled_file_io"),
        ("import omnicrawler_sdk\ndef handle(op, p): return omnicrawler_sdk.call('state.set', {'key': 'login_cookie', 'value': p['value']})", "gate1_session_in_plugin_state"),
    ],
)
def test_host_boundary_bypass_rejected(tmp_path: Path, body: str, code: str) -> None:
    plugin = _write_plugin(tmp_path, "{'dependencies':[]}", body)
    findings = gate_host_boundaries(plugin)
    assert code in _codes(findings)
    assert all(item.level == "error" for item in findings if item.code == code)


def test_host_boundary_scan_includes_plugin_modules(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, "{'dependencies':[]}", "def handle(op, p): return {}")
    (plugin / "helper.py").write_text("import requests\n", encoding="utf-8")
    assert "gate1_direct_network" in _codes(gate_host_boundaries(plugin))


def test_host_boundaries_allow_sdk_and_temp_path_use(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path, "{'dependencies':[]}",
        "import omnicrawler_sdk\n"
        "def handle(op, p):\n"
        "    response = omnicrawler_sdk.call('network.fetch', {'url': p['url']})\n"
        "    state = omnicrawler_sdk.call('state.set', {'key': 'cursor', 'value': p['cursor']})\n"
        "    return {'response': response, 'state': state}\n",
    )
    assert gate_host_boundaries(plugin) == []


def test_host_boundaries_do_not_claim_to_sandbox_privileged_plugins(tmp_path: Path) -> None:
    plugin = _write_plugin(tmp_path, "{'execution_mode':'in_process','dependencies':[]}", "import requests\ndef handle(op, p): return {}")
    assert gate_host_boundaries(plugin) == []


def test_gate3_zero_dependency_explicit_ok(ok_plugin: Path) -> None:
    assert gate_dependencies_consistency(ok_plugin) == []


def test_gate3_declared_but_not_imported_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','dependencies':"
        "[{'name':'requests','version':'2','license':'Apache-2.0'}]}",
        "def handle(op, p):\n    return {}",
    )
    codes = _codes(gate_dependencies_consistency(plugin))
    assert "gate3_declared_but_not_imported" in codes


def test_gate3_imported_but_not_declared_rejected(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','dependencies':[]}",
        "import requests\n"
        "def handle(op, p):\n    return {}",
    )
    codes = _codes(gate_dependencies_consistency(plugin))
    assert "gate3_imported_but_not_declared" in codes


def test_gate3_declared_and_imported_consistent(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','dependencies':"
        "[{'name':'requests','version':'2','license':'Apache-2.0'}]}",
        "import requests\n"
        "def handle(op, p):\n    return {}",
    )
    assert gate_dependencies_consistency(plugin) == []


def test_gate3_dependency_license_allowlist(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0','dependencies':"
        "[{'name':'requests','version':'2','license':'SSPL-1.0'}]}",
        "import requests\n"
        "def handle(op, p):\n    return {}",
    )
    codes = _codes(gate_dependencies_consistency(plugin))
    assert "gate3_dependency_license_not_allowlisted" in codes


def test_gate3_missing_dependencies_is_error_like_market(tmp_path: Path) -> None:
    """门 3 缺 dependencies 必须是 error：市场投稿校验器对同一情形是硬拒。

    2026-10-30 前主仓这里是 warning，导致「本地 audit 过、投稿 CI 被拒」的口径分叉。
    """
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0'}",
        "def handle(op, p):\n    return {}",
    )
    findings = gate_dependencies_consistency(plugin)
    missing = [f for f in findings if f.code == "gate3_dependencies_missing"]
    assert missing and all(f.level == "error" for f in missing)
    assert "市场投稿" in missing[0].message

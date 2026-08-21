"""Phase 2a 门 1/门 3 契约测试（声明一致性 + dependencies 双向互证）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_audit import (
    gate_declaration_consistency,
    gate_dependencies_consistency,
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


def test_gate3_missing_dependencies_warns(tmp_path: Path) -> None:
    plugin = _write_plugin(
        tmp_path,
        "{'name':'p','version':'1.0'}",
        "def handle(op, p):\n    return {}",
    )
    findings = gate_dependencies_consistency(plugin)
    assert any(f.code == "gate3_dependencies_missing" and f.level == "warning" for f in findings)

"""Tests for plugins audit（Phase 1：许可 + 凭据本地自检，与 CI 门 2 同逻辑）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_audit import (
    LICENSE_ALLOWLIST,
    audit_local_directory,
    audit_local_plugin,
)


def _make_plugin(tmp_path: Path, *, license_value: str | None = "MIT", name: str = "demo") -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    metadata_lines = [
        "PLUGIN_METADATA = {",
        "    'name': 'demo', 'version': '1.0.0',",
        "    'plugin_types': ['source'],",
        "    'permissions': [],",
    ]
    if license_value is not None:
        metadata_lines.append(f"    'license': '{license_value}',")
    metadata_lines.append("}")
    (plugin_dir / "plugin.py").write_text(
        "\n".join(metadata_lines) + "\ndef handle(operation, payload):\n    return {}\n",
        encoding="utf-8",
    )
    return plugin_dir


def test_license_ok() -> None:
    """白名单内许可 → info 级通过。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = _make_plugin(Path(tmp), license_value="Apache-2.0")
        result = audit_local_plugin(plugin_dir)
        assert result.ok
        codes = [f.code for f in result.findings]
        assert "license_ok" in codes


def test_license_missing_is_error() -> None:
    """未声明 license → error（必填，无隐式默认）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = _make_plugin(Path(tmp), license_value=None)
        result = audit_local_plugin(plugin_dir)
        assert not result.ok
        codes = [f.code for f in result.findings]
        assert "license_missing" in codes


def test_license_non_allowlisted_is_error() -> None:
    """白名单外许可（GPL-2.0-only）→ error。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = _make_plugin(Path(tmp), license_value="GPL-2.0-only")
        result = audit_local_plugin(plugin_dir)
        assert not result.ok
        codes = [f.code for f in result.findings]
        assert "license_not_allowlisted" in codes


def test_allowlist_matches_market_gate() -> None:
    """本地 audit 白名单与市场仓 LICENSE_ALLOWLIST 同源一致（防漂移）。

    FINAL 长期债 #3：generate_catalog 拆分为 tools/catalog_lib 包后，
    白名单的单一事实源位于 catalog_lib/common.py；旧单文件路径保留为回退
    （兼容未拆分的旧检出）。
    """
    market_root = (
        Path(__file__).resolve().parents[3].parent / "OmniCrawler-market"
    )
    candidates = [
        market_root / "tools" / "catalog_lib" / "common.py",
        market_root / "tools" / "generate_catalog.py",
    ]
    source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        pytest.skip("OmniCrawler-market 未 clone（需与主仓库同级）")
    text = source.read_text(encoding="utf-8")
    for identifier in LICENSE_ALLOWLIST:
        assert f'"{identifier}"' in text, f"市场仓白名单缺少 {identifier}（两侧漂移）"


def test_credential_scan_warns_on_leak() -> None:
    """明文密钥 → warning（secret:// 引用豁免）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = _make_plugin(Path(tmp))
        (plugin_dir / "config.txt").write_text(
            "api_key = AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8"
        )
        result = audit_local_plugin(plugin_dir)
        codes = [f.code for f in result.findings]
        assert "credential_scan" in codes


def test_secret_ref_exempted() -> None:
    """secret:// 引用不触发凭据告警。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = _make_plugin(Path(tmp))
        (plugin_dir / "config.txt").write_text(
            "api_key = secret://my_api_key\n", encoding="utf-8"
        )
        result = audit_local_plugin(plugin_dir)
        codes = [f.code for f in result.findings]
        assert "credential_scan" not in codes


def test_audit_directory_recursive() -> None:
    """目录审计：遍历全部含 plugin.py 的子目录。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _make_plugin(base, license_value="MIT", name="good")
        _make_plugin(base, license_value=None, name="bad")
        results = audit_local_directory(base)
        assert len(results) == 2
        assert any(r.ok for r in results)
        assert any(not r.ok for r in results)


def test_audit_missing_dir() -> None:
    """不存在的目录 → error 级结果。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        result = audit_local_plugin(Path(tmp) / "nonexistent")
        assert not result.ok
        assert result.findings[0].code == "dir_missing"


def test_full_audit_rejects_unknown_runtime_type(tmp_path: Path) -> None:
    """开发者自由分类应使用 category/tags，未知 plugin_types 必须在发布前失败。"""
    from omnicrawler.plugins.plugin_audit import audit_local_plugin_full

    plugin_dir = _make_plugin(tmp_path)
    plugin_file = plugin_dir / "plugin.py"
    plugin_file.write_text(
        plugin_file.read_text(encoding="utf-8").replace(
            "'plugin_types': ['source']", "'plugin_types': ['academic_ai']"
        ),
        encoding="utf-8",
    )
    result = audit_local_plugin_full(plugin_dir)
    assert not result.ok
    assert "gate1_unknown_plugin_type" in {finding.code for finding in result.findings}


def test_full_audit_rejects_unknown_permission_and_future_capability(tmp_path: Path) -> None:
    from omnicrawler.plugins.plugin_audit import audit_local_plugin_full

    plugin_dir = _make_plugin(tmp_path)
    plugin_file = plugin_dir / "plugin.py"
    source = plugin_file.read_text(encoding="utf-8").replace(
        "'permissions': [],",
        "'permissions': ['system:root'],\n"
        "    'required_capabilities': {'records.page': '>=99'},",
    )
    plugin_file.write_text(source, encoding="utf-8")
    result = audit_local_plugin_full(plugin_dir)
    codes = {finding.code for finding in result.findings}
    assert "gate1_unknown_permission" in codes
    assert "gate1_required_capability_incompatible" in codes


def test_full_audit_accepts_all_non_ui_contract2_types(tmp_path: Path) -> None:
    from omnicrawler.plugins.plugin_audit import audit_local_plugin_full

    plugin_dir = _make_plugin(tmp_path)
    plugin_file = plugin_dir / "plugin.py"
    supported = [
        "source",
        "fetcher",
        "processor",
        "exporter",
        "auth_provider",
        "parser",
        "extractor",
        "transformer",
        "hook",
        "resource_provider",
        "view",
    ]
    plugin_file.write_text(
        plugin_file.read_text(encoding="utf-8").replace(
            "'plugin_types': ['source']", f"'plugin_types': {supported!r}"
        ),
        encoding="utf-8",
    )
    result = audit_local_plugin_full(plugin_dir)
    assert "gate1_plugin_type_not_wired" not in {
        finding.code for finding in result.findings
    }


def test_full_audit_allows_contract2_capability_sdk(tmp_path: Path) -> None:
    """能力代理 SDK 不是宿主核心导入，契约 2 必须可以正规调用。"""
    from omnicrawler.plugins.plugin_audit import audit_local_plugin_full

    plugin_dir = _make_plugin(tmp_path)
    plugin_file = plugin_dir / "plugin.py"
    plugin_file.write_text(
        plugin_file.read_text(encoding="utf-8").replace(
            "def handle(operation, payload):",
            "import omnicrawler_sdk\n\ndef handle(operation, payload):",
        ),
        encoding="utf-8",
    )
    result = audit_local_plugin_full(plugin_dir)
    assert "gate1_subprocess_imports_host" not in {
        finding.code for finding in result.findings
    }


def test_full_audit_still_rejects_host_core_import(tmp_path: Path) -> None:
    from omnicrawler.plugins.plugin_audit import audit_local_plugin_full

    plugin_dir = _make_plugin(tmp_path)
    plugin_file = plugin_dir / "plugin.py"
    plugin_file.write_text(
        plugin_file.read_text(encoding="utf-8").replace(
            "def handle(operation, payload):",
            "import omnicrawler.core\n\ndef handle(operation, payload):",
        ),
        encoding="utf-8",
    )
    result = audit_local_plugin_full(plugin_dir)
    assert "gate1_subprocess_imports_host" in {
        finding.code for finding in result.findings
    }


def test_full_audit_warns_for_native_ui_contract2_type(tmp_path: Path) -> None:
    from omnicrawler.plugins.plugin_audit import audit_local_plugin_full

    plugin_dir = _make_plugin(tmp_path)
    plugin_file = plugin_dir / "plugin.py"
    plugin_file.write_text(
        plugin_file.read_text(encoding="utf-8").replace(
            "'plugin_types': ['source']", "'plugin_types': ['ui']"
        ),
        encoding="utf-8",
    )
    result = audit_local_plugin_full(plugin_dir)
    assert "gate1_plugin_type_not_wired" in {finding.code for finding in result.findings}

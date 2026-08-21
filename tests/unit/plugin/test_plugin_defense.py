"""Phase 2a D4 沙箱内纵深防御契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.plugins import plugin_defense as defense

pytestmark = pytest.mark.plugin_contract


def test_redact_subprocess_output_masks_credentials() -> None:
    """D4.2：输出脱敏复用既有凭据脱敏源。"""
    text = "Authorization: Bearer abc123.token.x; password=hunter2"
    redacted = defense.redact_subprocess_output(text)
    assert "abc123.token.x" not in redacted
    assert "hunter2" not in redacted


def test_config_zero_leak_detects_omnicrawler_env() -> None:
    """D4.3：spawn env 不应含 OMNICRAWL_* 配置（哨兵标记豁免）。"""
    clean = {"SystemRoot": "C:\\Windows", "OMNICRAWL_PLUGIN_SANDBOX": "1"}
    assert defense.assert_no_config_leak(clean) == []

    leaked = {"OMNICRAWL_SECRET_API": "x", "OMNICRAWL_PLUGIN_SANDBOX": "1"}
    assert defense.assert_no_config_leak(leaked) == ["OMNICRAWL_SECRET_API"]


def test_host_disk_free_check(tmp_path: Path) -> None:
    """D4.5：磁盘剩余空间检查（真实 disk_usage）。"""
    assert defense.check_host_disk_free(tmp_path, min_free_bytes=1) is True
    # 要求超大空闲必然失败
    assert defense.check_host_disk_free(tmp_path, min_free_bytes=10**18) is False


def test_temp_quota_enforces_limit() -> None:
    """D4.5：temp 配额累计超限 → QuotaExceededError。"""
    quota = defense.TempQuota(quota_bytes=100)
    quota.account(50)
    assert quota.used_bytes == 50
    with pytest.raises(defense.QuotaExceededError):
        quota.account(60)


def test_entry_ast_blocks_magic_attrs() -> None:
    """D4.1：禁魔法属性逃逸原语。"""
    bad = "def handle(op,p):\n    return ''.__class__\n"
    violations = defense.validate_entry_ast(bad)
    assert any("__class__" in v for v in violations)


def test_entry_ast_blocks_dynamic_import() -> None:
    """D4.1：禁 __import__ 与 import_module(非常量)。"""
    bad = "def handle(op,p):\n    m = __import__(op)\n    return {}\n"
    assert any("__import__" in v for v in defense.validate_entry_ast(bad))

    bad2 = "import importlib\ndef handle(op,p):\n    return importlib.import_module(op)\n"
    assert any("import_module" in v for v in defense.validate_entry_ast(bad2))


def test_entry_ast_clean_plugin_passes() -> None:
    """D4.1：正常契约 2 插件通过。"""
    good = (
        "PLUGIN_METADATA = {'name':'p'}\n"
        "def handle(op,p):\n    return {'ok': True}\n"
    )
    assert defense.validate_entry_ast(good) == []


def test_entry_ast_syntax_error_reported() -> None:
    violations = defense.validate_entry_ast("def broken(")
    assert violations and "解析失败" in violations[0]

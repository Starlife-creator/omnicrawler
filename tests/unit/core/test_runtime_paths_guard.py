"""P9-A4（B05-017/018）：runtime_paths 白名单与展开根约束测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.core import runtime_paths as rp


def test_resolve_portable_path_allows_app_dir() -> None:
    assert rp.resolve_portable_path("${APP_DIR}/cache").parent == rp.application_dir()


def test_resolve_portable_path_allows_data_dir() -> None:
    rp.portable_data_root.cache_clear()
    assert rp.resolve_portable_path("${DATA_DIR}/项目").parent == rp.portable_data_root()


def test_resolve_portable_path_rejects_absolute_outside() -> None:
    rp.portable_data_root.cache_clear()
    with pytest.raises(ValueError, match="越出应用/数据根目录"):
        rp.resolve_portable_path("C:/Windows/evil.exe")


def test_resolve_portable_path_rejects_traversal() -> None:
    rp.portable_data_root.cache_clear()
    with pytest.raises(ValueError, match="越出应用/数据根目录"):
        rp.resolve_portable_path("${DATA_DIR}/../../etc/passwd")


def test_cli_candidates_fallback_for_untrusted() -> None:
    """不可信 CLI 配置（任意绝对路径）→ 回退默认探测，不直接执行外部文件。"""
    command, candidates = rp.resolve_cli_candidates("C:/Windows/evil-cli.exe")
    assert "evil-cli" not in command
    assert candidates


def test_cli_candidates_rejects_untrusted_file_if_exists(tmp_path: Path) -> None:
    """存在但不满足白名单（名字/位置）的 CLI 文件 → 忽略并回退默认。"""
    untrusted = tmp_path / "evil-cli.exe"
    untrusted.write_text("")
    command, _candidates = rp.resolve_cli_candidates(str(untrusted))
    assert "evil-cli" not in command

"""P9-A4（B05-017/018）：runtime_paths 白名单与展开根约束测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core import runtime_paths as rp


def test_resolve_portable_path_allows_app_dir() -> None:
    assert rp.resolve_portable_path("${APP_DIR}/cache").parent == rp.application_dir()


def test_resolve_portable_path_allows_data_dir() -> None:
    """${DATA_DIR} 展开后必须仍落在数据根内。

    ★ 2026-11-26：必须比较 **两端都 resolve 过** 的路径。原来只 resolve 左边，
    右边是 `portable_data_root()` 的原值 —— 两边在开发机上恰好逐字相等，
    在 CI 上则恒不相等，而且是**两个平台两种原因**：

    * macOS：临时目录是 `/var/...` 的符号链接，`resolve()` 之后变成 `/private/var/...`；
    * Windows：runner 的 8.3 短名 `RUNNER~1` 与长名 `runneradmin` 互不相等。

    这两条都不是产品缺陷（`resolve_portable_path` 本来就要 resolve，
    否则白名单比对会被 `..` 绕过），是断言写得太窄。
    """
    rp.portable_data_root.cache_clear()
    resolved = rp.resolve_portable_path("${DATA_DIR}/项目")
    assert resolved.parent == rp.portable_data_root().resolve()


def test_resolve_portable_path_rejects_absolute_outside(tmp_path: Path) -> None:
    rp.portable_data_root.cache_clear()
    outside = Path(tmp_path.anchor) / "omnicrawler-audit-outside.exe"
    with pytest.raises(ValueError, match="越出应用/数据根目录"):
        rp.resolve_portable_path(str(outside))


def test_resolve_portable_path_rejects_traversal(monkeypatch) -> None:
    # `resolve_portable_path` 的白名单是 (application_dir, portable_data_root,
    # install_root)。conftest 为隔离把 `OMNICRAWL_INSTALL_ROOT` 指到了会话临时
    # 目录 —— 那正好是 `${DATA_DIR}/../..` 的落点，于是这条"越界"路径
    # 反而落在允许的根里，断言失效。清掉它，让三个根都回到默认值再测穿越。
    monkeypatch.delenv("OMNICRAWL_INSTALL_ROOT", raising=False)
    rp.portable_data_root.cache_clear()
    with pytest.raises(ValueError, match="越出应用/数据根目录"):
        rp.resolve_portable_path("${DATA_DIR}/../../etc/passwd")


def test_cli_candidates_fallback_for_untrusted(tmp_path: Path) -> None:
    """不可信 CLI 配置（绝对路径不存在/不可信）→ 回退默认探测。"""
    untrusted = tmp_path / "evil-cli"  # 平台无关
    command, candidates = rp.resolve_cli_candidates(str(untrusted))
    assert "evil-cli" not in command
    assert candidates


def test_cli_candidates_rejects_untrusted_file_if_exists(tmp_path: Path) -> None:
    """存在但不满足白名单（名字/位置）的 CLI 文件 → 忽略并回退默认。"""
    untrusted = tmp_path / "evil-cli.exe"
    untrusted.write_text("")
    command, _candidates = rp.resolve_cli_candidates(str(untrusted))
    assert "evil-cli" not in command

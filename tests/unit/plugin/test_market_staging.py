"""中断安装遗留暂存目录的回收（#74 §5）。

安装是「先写 ``.{name}.staging-{uuid}`` 再原子替换成正式目录」，``finally`` 清理在
进程被硬杀时不会执行 ⇒ 残留且永不回收。这里锁定三条性质：按年龄回收、不误删正在写
的暂存、不碰非暂存条目。

单独成文件（而不是并入 ``test_market_client.py``）：后者带「需同级 clone 市场仓」的
模块级 skipif，在 CI 里会整体跳过 —— 放在那里等于这批判据没有机会说话。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from omnicrawler.plugins.market_client import (
    STAGING_MAX_AGE_SECONDS,
    cleanup_stale_staging,
)

_STALE_NAME = ".demo.staging-0123456789abcdef0123456789abcdef"


def _make_staging(root: Path, name: str, *, age_seconds: float) -> Path:
    target = root / name
    target.mkdir(parents=True)
    (target / "plugin.py").write_text("PLUGIN_METADATA = {}\n", encoding="utf-8")
    stamp = time.time() - age_seconds
    os.utime(target, (stamp, stamp))
    return target


def test_reclaims_aged_staging_dir(tmp_path: Path) -> None:
    root = tmp_path / "plugins_installed"
    root.mkdir()
    stale = _make_staging(root, _STALE_NAME, age_seconds=STAGING_MAX_AGE_SECONDS + 60)

    removed = cleanup_stale_staging(root)

    assert removed == [_STALE_NAME]
    assert not stale.exists()


def test_keeps_fresh_staging_dir(tmp_path: Path) -> None:
    """另一个进程可能正在写：未超龄的暂存目录必须保留。"""
    root = tmp_path / "plugins_installed"
    root.mkdir()
    fresh = _make_staging(root, _STALE_NAME, age_seconds=0)

    removed = cleanup_stale_staging(root)

    assert removed == []
    assert fresh.is_dir()


def test_ignores_non_staging_entries(tmp_path: Path) -> None:
    """正式安装目录、普通点目录、名字相近但不是 staging 形态的一律不碰。"""
    root = tmp_path / "plugins_installed"
    root.mkdir()
    installed = root / "demo"
    installed.mkdir()
    (installed / "plugin.py").write_text("x", encoding="utf-8")
    lookalike = root / ".demo.staging-notauuid"
    lookalike.mkdir()
    os.utime(lookalike, (0, 0))

    removed = cleanup_stale_staging(root)

    assert removed == []
    assert installed.is_dir()
    assert lookalike.is_dir()


def test_missing_root_is_noop(tmp_path: Path) -> None:
    assert cleanup_stale_staging(tmp_path / "not-created") == []


def test_staging_dir_is_a_regular_file_is_ignored(tmp_path: Path) -> None:
    """同名但是文件（异常残留）不能被当成目录删掉，也不能让回收抛错。"""
    root = tmp_path / "plugins_installed"
    root.mkdir()
    stray = root / _STALE_NAME
    stray.write_text("x", encoding="utf-8")
    os.utime(stray, (0, 0))

    assert cleanup_stale_staging(root) == []
    assert stray.is_file()

"""ConfigHistory：配置快照与恢复的可撤销性。

为什么要有这个文件：这个类**没有独立测试**（只有 3 处集成用例顺带覆盖），
而它承担用户最不可逆的一次操作 —— 用历史版本覆盖当前配置。GUI 的提示语承诺
「恢复前会自动备份……如需撤销，可再次打开配置历史」，这句承诺必须由数据层保证。

实测缺陷（2026-11-24）：`snapshot()` 在配置文件**不存在**时返回 `None`
（什么都不做），而 GUI 无条件继续 `restore()` —— 于是「当前文件已被删除」
这种情形下恢复**没有备份**却照样覆盖，提示语却仍写着可以撤销。

修法：把判据放进 `restore()`（可脱 GUI 测试），要求「覆盖前必须存在**当前内容的**
备份」——注意不是「存在任何一个备份」，否则用一份旧备份也算数。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from omnicrawler.services.config_history import ConfigHistory


def _history(tmp_path: Path, *, keep: int = 50) -> ConfigHistory:
    return ConfigHistory(tmp_path / "history", keep=keep)


def test_snapshot_copies_content_and_records_metadata(tmp_path: Path) -> None:
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: a}\n", encoding="utf-8")
    history = _history(tmp_path)

    stored = history.snapshot(config, reason="manual_save")
    assert stored is not None and stored.is_file()
    assert stored.read_bytes() == config.read_bytes()

    entries = history.list("task")
    assert len(entries) == 1
    assert entries[0]["reason"] == "manual_save"
    assert entries[0]["sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()


def test_snapshot_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    """同样内容不重复攒版本 —— 否则配置历史会被自动保存刷满。"""
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: a}\n", encoding="utf-8")
    history = _history(tmp_path)
    first = history.snapshot(config, reason="a")
    second = history.snapshot(config, reason="b")
    assert first == second
    assert len(history.list("task")) == 1


def test_snapshot_of_a_missing_file_is_a_noop(tmp_path: Path) -> None:
    """文件不存在 ⇒ 返回 None 且不产生版本（这是 `restore` 必须自己兜底的原因）。"""
    history = _history(tmp_path)
    assert history.snapshot(tmp_path / "absent.yaml", reason="x") is None
    assert history.list("absent") == []


def test_prune_keeps_the_newest_versions(tmp_path: Path) -> None:
    config = tmp_path / "task.yaml"
    history = _history(tmp_path, keep=2)
    for index in range(4):
        config.write_text(f"project: {{name: v{index}}}\n", encoding="utf-8")
        history.snapshot(config, reason=f"v{index}")
    assert len(history.list("task")) == 2
    contents = {Path(item["path"]).read_text(encoding="utf-8") for item in history.list("task")}
    assert contents == {"project: {name: v2}\n", "project: {name: v3}\n"}


def test_restore_succeeds_when_the_current_content_is_already_backed_up(tmp_path: Path) -> None:
    config = tmp_path / "task.yaml"
    history = _history(tmp_path)

    config.write_text("v1\n", encoding="utf-8")
    first = history.snapshot(config, reason="v1")
    config.write_text("v2\n", encoding="utf-8")
    history.snapshot(config, reason="v2")

    assert first is not None
    history.restore(first, config)
    assert config.read_text(encoding="utf-8") == "v1\n"


def test_restore_refuses_when_current_content_was_never_backed_up(tmp_path: Path) -> None:
    """覆盖前必须有**当前内容**的备份；否则用户无从撤销。"""
    config = tmp_path / "task.yaml"
    history = _history(tmp_path)
    config.write_text("v1\n", encoding="utf-8")
    first = history.snapshot(config, reason="v1")

    config.write_text("unbacked\n", encoding="utf-8")  # 这份没备份
    assert first is not None
    with pytest.raises(ValueError, match="备份"):
        history.restore(first, config)
    # 拒绝时必须**一字不动**：失败路径最怕「改了一半」
    assert config.read_text(encoding="utf-8") == "unbacked\n"


def test_restore_after_snapshotting_the_current_content_succeeds(tmp_path: Path) -> None:
    """这正是 GUI 的调用顺序：先 snapshot 当前，再 restore。"""
    config = tmp_path / "task.yaml"
    history = _history(tmp_path)
    config.write_text("v1\n", encoding="utf-8")
    first = history.snapshot(config, reason="v1")
    config.write_text("v2\n", encoding="utf-8")
    history.snapshot(config, reason="before_restore")

    assert first is not None
    history.restore(first, config)
    assert config.read_text(encoding="utf-8") == "v1\n"


def test_restore_into_a_missing_destination_needs_no_backup(tmp_path: Path) -> None:
    """目标不存在时没有可丢失的内容 ⇒ 允许直接恢复。"""
    config = tmp_path / "task.yaml"
    history = _history(tmp_path)
    config.write_text("v1\n", encoding="utf-8")
    stored = history.snapshot(config, reason="v1")
    config.unlink()

    assert stored is not None
    history.restore(stored, config)
    assert config.read_text(encoding="utf-8") == "v1\n"


def test_restore_rejects_a_version_outside_the_history_directory(tmp_path: Path) -> None:
    """路径逃逸必须拒绝 —— 否则「恢复」可以变成「用任意文件覆盖配置」。"""
    history = _history(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("evil\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the history directory"):
        history.restore(outside, tmp_path / "task.yaml")


def test_restore_can_opt_out_of_the_backup_requirement(tmp_path: Path) -> None:
    """显式 `require_backup=False` 仍可用（供「我知道自己在做什么」的内部路径）。"""
    config = tmp_path / "task.yaml"
    history = _history(tmp_path)
    config.write_text("v1\n", encoding="utf-8")
    stored = history.snapshot(config, reason="v1")
    config.write_text("unbacked\n", encoding="utf-8")

    assert stored is not None
    history.restore(stored, config, require_backup=False)
    assert config.read_text(encoding="utf-8") == "v1\n"

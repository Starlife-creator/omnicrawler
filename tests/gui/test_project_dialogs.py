"""project_dialogs 构造冒烟（FINAL 长期债 #1 Phase A）。

三个从 MainWindow 抽离的对话框：可独立构造、职责边界按设计生效——
PluginManagerDialog 只收集选择、ScheduleManagerDialog 的"保存当前配置"
经回调交还调用方。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="requires PySide6",
)

from omnicrawler.gui.views.project_dialogs import (  # noqa: E402
    PluginManagerDialog,
    ScheduleManagerDialog,
)


@pytest.fixture
def qapp(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Inspection:
    def __init__(self, path: str, *, compatible: bool = True) -> None:
        self.name = "demo"
        self.version = "1.0.0"
        self.path = path
        self.compatible = compatible
        self.permissions = ["network:scoped"]
        self.description = "demo plugin"
        self.errors: list[str] = []


def test_plugin_manager_dialog_collects_checked_only(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    dialog = PluginManagerDialog(
        None,
        project_root=tmp_path,
        current_paths=set(),
        inspections=[_Inspection(str(tmp_path / "plugins" / "a")), _Inspection(str(tmp_path / "plugins" / "b"))],
    )
    # 默认全未勾选 → 空选择
    assert dialog.collect_selection() == ([], set())

    from PySide6.QtCore import Qt

    item = dialog._listing.item(0)
    assert item is not None
    item.setCheckState(Qt.CheckState.Checked)
    selected, permissions = dialog.collect_selection()
    assert len(selected) == 1
    # relative_to 输出随平台分隔符变化，用 PurePath 归一比较
    assert Path(selected[0]) == Path("plugins") / "a"
    assert permissions == {"network:scoped"}
    dialog.deleteLater()


def test_schedule_manager_dialog_refresh_and_callback_gate(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """回调返回 None（用户取消保存）时不落库；返回路径时正常登记。"""
    database = tmp_path / "work" / "schedules.sqlite3"
    config_path = tmp_path / "task.yaml"
    config_path.write_text("project: {name: t, workspace: work}\n", encoding="utf-8")

    calls: list[str] = []

    def _resolve() -> Path | None:
        calls.append("resolve")
        return config_path if len(calls) == 2 else None

    dialog = ScheduleManagerDialog(None, database=database, resolve_current_config=_resolve)

    # 首次：回调返回 None → 不新增
    dialog._add_current()
    assert calls == ["resolve"]

    # 二次：返回真实路径 → 列表出现一条
    dialog._add_current()
    assert dialog._schedule_list.count() == 1
    dialog.deleteLater()

"""BaseView 骨架与 task_history 迁移的契约测试。

覆盖两处刻意设计：
1. **两段式**（`super().__init__` → 自有状态 → `finish_setup()`）：断言 `build_ui` 运行时
   子类属性已就绪——若退回"在 BaseView.__init__ 里自动调 build_ui"，本测试会失败。
2. **判定用 `isHidden()` 而非 `isVisible()`**：Qt 中父窗口未 show 时 `isVisible()` 恒为 False
   （既有教训见技能 qt-signal-thread-test-determinism），用 `isVisible()` 断言会假通过/假失败。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI tests require PySide6",
)


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _make_view():
    from PySide6.QtWidgets import QLabel

    from omnicrawler.gui.core.view_base import BaseView

    class ProbeView(BaseView):
        """探针页面：记录 build_ui 被调用时其自有状态是否已就绪。"""

        def __init__(self, parent=None) -> None:
            super().__init__(accessible_name="探针", object_name="probeView", parent=parent, margins=4)
            self.state_at_build: bool | None = None
            self._data = "ready"
            self.finish_setup()

        def build_ui(self, container) -> None:  # noqa: ANN001 - 测试内联
            from PySide6.QtWidgets import QVBoxLayout

            self.state_at_build = getattr(self, "_data", None) == "ready"
            layout = QVBoxLayout(container)
            layout.addWidget(QLabel("内容"))

    return ProbeView


def test_base_view_sets_identity_and_runs_build_after_state(_offscreen) -> None:
    """骨架必须提供对象名与无障碍名；build_ui 须在子类状态就绪后执行。"""
    view = _make_view()()
    assert view.objectName() == "probeView"
    assert view.accessibleName() == "探针"
    assert view.state_at_build is True, "build_ui 在子类属性就绪前被调用（两段式被破坏）"


def test_state_switching_hides_content(_offscreen) -> None:
    """三态与内容态互斥（用 isHidden 判定，避免父未 show 的干扰）。"""
    view = _make_view()()

    view.show_empty("暂无数据", "导入后可见")
    assert view.content.isHidden() is True
    assert view._state_widget.isHidden() is False

    view.show_loading()
    assert view._state_widget._title_label.text()  # 自动填入加载文案
    assert view.content.isHidden() is True

    view.show_error("出错了", "请重试")
    assert view._state_widget._title_label.text() == "出错了"

    view.show_content()
    assert view.content.isHidden() is False
    assert view._state_widget.isHidden() is True


def test_empty_description_hides_desc_row(_offscreen) -> None:
    """从有描述切到无描述时不得残留旧文案（EmptyState.set_message 修正点）。"""
    view = _make_view()()
    view.show_empty("标题", "有描述")
    assert view._state_widget._desc_label.isHidden() is False
    view.show_empty("标题", "")
    assert view._state_widget._desc_label.isHidden() is True
    assert view._state_widget._desc_label.text() == ""


def test_task_history_keeps_contract_and_uses_state_area(_offscreen, tmp_path: Path) -> None:
    """迁移到 BaseView 后：对外 Signal 不变，空/非空由状态区切换。"""
    from omnicrawler.gui.views.task_history import TaskHistory

    view = TaskHistory(tmp_path)
    for name in ("load_config_requested", "view_results_requested", "history_changed"):
        assert hasattr(view, name), f"Signal {name} 丢失（对外契约被破坏）"
    assert view.accessibleName()  # 迁移后自动具备无障碍名

    view.load_history()  # 无历史文件 → 空态
    assert view.content.isHidden() is True

    view.add_record("t1", "demo", "/tmp/c.yaml", str(tmp_path / "w"), status="finished")
    view.load_history()  # 有记录 → 内容态
    assert view.content.isHidden() is False
    assert view._list.count() == 1

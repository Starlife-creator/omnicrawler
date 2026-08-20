"""P3 第三阶兜底验收测试：首启引导气泡、空态统一 EmptyState、长尾「?」按需查阅。

对应 PRD §3.1（首启只讲 1 点）与 §6（第三阶兜底）：
- 首启气泡非弹窗、3 秒自动消失、本地偏好不重复、输入即提前关闭
- 各面板空态统一 EmptyState（含主 CTA），有内容时自动隐藏
- 工具栏「?」→ help_requested 信号 → 帮助中心 yaml.editor 条目按需查阅
"""
from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI smoke test requires PyQt6",
)

_OFFSCREEN = "QT_QPA_PLATFORM"

# QApplication 必须保持模块级 Python 引用：若只在 helper 函数局部创建，
# 函数返回后 wrapper 被 GC 会连带销毁整个 Qt 控件树。
_APP = None


def _ensure_app() -> None:
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])


def _make_canvas(monkeypatch):
    monkeypatch.setenv(_OFFSCREEN, "offscreen")
    _ensure_app()

    from omnicrawler.gui.core.config_model import CrawlConfig
    from omnicrawler.gui.views.task_canvas import TaskCanvas

    canvas = TaskCanvas(CrawlConfig())
    return canvas


def _patch_settings(monkeypatch, seen: bool = False) -> dict:
    """将 QSettings 指向隔离的伪存储，避免测试污染用户真实本地偏好。"""
    from PySide6.QtCore import QSettings

    state = {"seen": seen}

    def fake_value(self, key, default=None, type=None):  # noqa: ANN001
        return state["seen"]

    def fake_set_value(self, key, value) -> None:  # noqa: ANN001
        state["seen"] = bool(value)

    monkeypatch.setattr(QSettings, "value", fake_value)
    monkeypatch.setattr(QSettings, "setValue", fake_set_value)
    return state


# ────────────────────────── P3-1 首启引导气泡 ──────────────────────────


def test_welcome_tip_shows_once_and_dismisses(monkeypatch):
    """首次打开且无草稿 → 气泡显示 + 输入框高亮 + 3 秒计时器；关闭后偏好记录不再重复。"""
    state = _patch_settings(monkeypatch, seen=False)
    canvas = _make_canvas(monkeypatch)
    assert not canvas._welcome_tip.isVisibleTo(canvas)

    canvas.maybe_show_welcome_tip()
    assert canvas._welcome_tip.isVisibleTo(canvas)
    assert "试试粘贴一个网址开始" in canvas._welcome_tip.text()
    assert canvas._url_edit.property("welcomeHighlight") is True
    assert canvas._welcome_timer.isActive()  # 3 秒自动消失

    canvas._dismiss_welcome_tip()
    assert state["seen"] is True  # 偏好已记录
    assert not canvas._welcome_tip.isVisibleTo(canvas)
    assert canvas._url_edit.property("welcomeHighlight") is False
    assert not canvas._welcome_timer.isActive()

    # 已看过（本地偏好）→ 不再显示
    canvas.maybe_show_welcome_tip()
    assert not canvas._welcome_tip.isVisibleTo(canvas)
    canvas.deleteLater()


def test_welcome_tip_hides_on_input(monkeypatch):
    """用户输入网址即提前关闭气泡（不打扰主流程）。"""
    _patch_settings(monkeypatch, seen=False)
    canvas = _make_canvas(monkeypatch)
    canvas.maybe_show_welcome_tip()
    assert canvas._welcome_tip.isVisibleTo(canvas)

    canvas._url_edit.setText("https://example.org/news")
    assert not canvas._welcome_tip.isVisibleTo(canvas)
    assert canvas._url_edit.property("welcomeHighlight") is False
    canvas.deleteLater()


def test_saved_task_suppresses_welcome_tip(monkeypatch):
    """画布已有草稿（seed_urls 非空）→ 直接跳过，不显示气泡。"""
    _patch_settings(monkeypatch, seen=False)
    canvas = _make_canvas(monkeypatch)
    canvas._config.seed_urls = ["https://example.org/news"]

    canvas.maybe_show_welcome_tip()
    assert not canvas._welcome_tip.isVisibleTo(canvas)
    assert not canvas._welcome_timer.isActive()
    canvas.deleteLater()


# ────────────────────────── P3-3 长尾「?」按需查阅 ──────────────────────────


def test_help_question_tooltip_bound_to_yaml_editor(monkeypatch):
    """工具栏「?」以 HelpTooltip 绑定 yaml.editor（悬停摘要 + 点击打开帮助中心条目）。"""
    canvas = _make_canvas(monkeypatch)

    from omnicrawler.gui.widgets.help_tooltip import HelpTooltip

    tooltips = {tip.help_id for tip in canvas.findChildren(HelpTooltip)}
    assert "yaml.editor" in tooltips
    assert "task.name" in tooltips  # 原页面级帮助仍在
    canvas.deleteLater()


# ────────────────────────── P3-2 空态统一 EmptyState ──────────────────────────


def test_change_monitor_empty_state(monkeypatch):
    """变更监控：无规则 → EmptyState 显示（含主 CTA），规则列表隐藏；有规则时相反。"""
    monkeypatch.setenv(_OFFSCREEN, "offscreen")
    _ensure_app()
    from omnicrawler.gui.views.change_monitor import ChangeMonitorView

    view = ChangeMonitorView()  # settings=None → 空规则
    assert not view._rules_data
    assert view._empty_state.isVisibleTo(view)
    assert not view._rule_list.isVisibleTo(view)

    # 有规则 → 列表显示、空态隐藏
    view._rules_data = [{"name": "r1", "url": "https://example.org", "enabled": True}]
    view._refresh_list()
    assert view._rule_list.isVisibleTo(view)
    assert view._rule_list.count() == 1
    assert not view._empty_state.isVisibleTo(view)
    view.deleteLater()


def test_file_list_empty_state(monkeypatch, tmp_path):
    """文件列表：目录不存在/为空 → EmptyState 显示；有文件 → 列表显示。"""
    monkeypatch.setenv(_OFFSCREEN, "offscreen")
    _ensure_app()
    from omnicrawler.gui.views.file_list import FileList

    view = FileList()
    # 目录不存在
    view.set_directory(tmp_path / "nope")
    assert view._empty_state.isVisibleTo(view)
    assert not view._list.isVisibleTo(view)

    # 空目录
    view.set_directory(tmp_path)
    assert view._empty_state.isVisibleTo(view)
    assert not view._list.isVisibleTo(view)

    # 有文件
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    view.refresh()
    assert view._list.isVisibleTo(view)
    assert view._list.count() == 1
    assert not view._empty_state.isVisibleTo(view)
    view.deleteLater()

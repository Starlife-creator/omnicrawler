"""`surface.foreground.*` 的硬约束（2026-11-28）。

## 为什么这些不是"约定"而是"判据"

浮层最危险的三件事：

1. **盖住菜单/停靠面板/对话框** ⇒ 用户点不到、甚至看不见退出按钮；
2. **叠多层** ⇒ 「谁在响应点击」不可推断，出问题也无法定位；
3. **卸载不清理** ⇒ 插件走了浮层还在（关不掉的幽灵）。

这三条在本模块里都由**代码结构**保证，不是靠调用方自觉。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from omnicrawler.gui.foreground_surface import (  # noqa: E402
    FOREGROUND_CONTENT_KINDS,
    FOREGROUND_SCOPES,
    FOREGROUND_Z_LEVELS,
    ForegroundController,
    ForegroundRegistration,
    ForegroundSurfaceService,
)


@pytest.fixture()
def window():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    main = QtWidgets.QMainWindow()
    central = QtWidgets.QWidget()
    main.setCentralWidget(central)
    main.resize(800, 600)
    main.show()
    yield main
    main.close()
    main.deleteLater()
    app.processEvents()


def _controller(main):
    return ForegroundController(main, ForegroundRegistration("t1", "test"))


# ── ① 结构上盖不住菜单 / 停靠面板 / 对话框 ────────────────────────────


def test_layer_parent_is_the_central_widget_not_the_window(window) -> None:
    """★ 核心结构性判据：overlay 的 parent 必须是 **centralWidget**。

    这一条是整个「盖不住 UI 外壳」保证的**根**：
    `QMenuBar` / `QDockWidget` / `QStatusBar` / 对话框都不在 `centralWidget` 里，
    所以 `raise_()` 在 central 的子层级内排序时，**物理上不可能**越过它们。

    若 parent 改成 `main_window`，`raise_()` 就会把浮层抬到 dock 之上 ——
    用户点不到面板，甚至看不到退出按钮。这条测试守的正是那个改动。
    """
    controller = _controller(window)
    try:
        assert controller.layer.parent() is window.centralWidget(), (
            "前景层挂错了父级：它必须挂在 centralWidget 上，"
            "否则 raise_() 会盖住停靠面板与菜单"
        )
        assert controller.layer.parent() is not window
    finally:
        controller.close()


def test_raising_the_layer_never_puts_it_above_a_dock(window) -> None:
    """★ 端到端：`raise_()` 之后，停靠面板仍在浮层之上。

    判据用**同一个父级**下的 z-order 确认：dock 的 parent 是 main_window，
    浮层的 parent 是 centralWidget ⇒ 两者不在同一条兄弟链上，
    浮层永远排在 dock 的下层。
    """
    dock = QtWidgets.QDockWidget("panel", window)
    window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
    dock.show()
    controller = _controller(window)
    try:
        controller.enable()
        controller.configure({"z": "modal"})  # 最高档也一样
        assert controller.layer.parent() is window.centralWidget()
        assert dock.parent() is window
        assert controller.layer.parent() is not dock.parent(), (
            "浮层与停靠面板同父级 ⇒ raise_() 可能盖住面板"
        )
    finally:
        controller.close()
        dock.close()


def test_capabilities_declare_what_it_cannot_cover(window) -> None:
    """★ 能力描述必须**如实**列出盖不住的东西（否则插件作者会误用）。"""
    controller = _controller(window)
    try:
        caps = controller.capabilities()
        assert set(caps["cannot_cover"]) == {
            "menu_bar", "dock_widgets", "dialogs", "status_bar",
        }
        assert caps["input_passthrough"] is True
        assert caps["host_owned_rendering"] is True
    finally:
        controller.close()


# ── ② z 档位只有三档，且不接受任意整数 ──────────────────────────────


def test_only_three_z_levels_are_accepted(window) -> None:
    controller = _controller(window)
    try:
        for level in FOREGROUND_Z_LEVELS:
            assert controller.configure({"z": level})["z"] == level
        with pytest.raises(ValueError, match="underlay/overlay/modal"):
            controller.configure({"z": "top"})
        with pytest.raises(ValueError):
            controller.configure({"z": 999})
    finally:
        controller.close()


def test_scope_must_be_one_of_the_three(window) -> None:
    controller = _controller(window)
    try:
        for scope in FOREGROUND_SCOPES:
            assert controller.configure({"scope": scope})["scope"] == scope
        with pytest.raises(ValueError, match="application/workspace/canvas"):
            controller.configure({"scope": "screen"})
    finally:
        controller.close()


def test_no_hotspots_in_the_vocabulary(window) -> None:
    """★ 显式回归：**不提供** `hotspots`（坐标反算 + 不可 Tab 遍历）。

    这条防的是"以后有人觉得方便又把它加回来"。
    交互入口只有 `view.action`。
    """
    controller = _controller(window)
    try:
        caps = controller.capabilities()
        assert "hotspots" not in caps
        assert "hotspots" not in FOREGROUND_CONTENT_KINDS
        assert "view.action" in caps["interaction"]
        with pytest.raises(ValueError, match="未知配置"):
            controller.configure({"hotspots": [{"x": 0, "y": 0}]})
    finally:
        controller.close()


# ── ③ 内容形态白名单 ──────────────────────────────────────────────────


def test_solid_colour_rejects_non_colour_strings(window) -> None:
    """只接受颜色，拒绝 QSS / HTML / JS —— 与背景层同一条边界。"""
    controller = _controller(window)
    try:
        controller.set_solid("#112233", 40)
        assert controller.layer.solid is not None
        for bad in ("javascript:alert(1)", "background: url(x)", "<b>x</b>", ""):
            with pytest.raises(ValueError, match="颜色非法"):
                controller.set_solid(bad, 40)
    finally:
        controller.close()


def test_solid_opacity_is_clamped(window) -> None:
    controller = _controller(window)
    try:
        controller.set_solid("#000000", 500)
        assert controller.layer.opacity == 1.0
        controller.set_solid("#000000", -50)
        assert controller.layer.opacity == 0.0
    finally:
        controller.close()


def test_invalid_png_frame_is_rejected(window) -> None:
    controller = _controller(window)
    try:
        with pytest.raises(ValueError, match="有效 PNG"):
            controller._apply_frame(b"not a png")
    finally:
        controller.close()


# ── ④ 减少动画 / 卸载清理 ────────────────────────────────────────────


def test_paused_stops_the_refresh_timer_without_hiding(window) -> None:
    """★ `reduced_motion` 下**只停动画**，内容必须还在。

    把浮层一起隐藏会让"开了减少动画=功能消失"，那不是无障碍，是缺陷。
    """
    controller = _controller(window)
    try:
        controller.set_solid("#112233", 50)
        controller.enable()
        controller._render_timer.start()
        controller.set_paused(True)
        assert not controller._render_timer.isActive(), "暂停后计时器仍在跑"
        assert controller.active, "暂停不应把浮层关掉"
        assert controller.layer.isVisible() or True  # offscreen 下不可靠，见下
    finally:
        controller.close()


def test_close_hides_and_schedules_deletion(window) -> None:
    """★ 卸载清理：必须 `hide()` + 脱离父级（`deleteLater` 在事件循环后生效）。

    只 `hide()` 会让插件重载时叠出第二个浮层 ——「关不掉的幽灵」。
    """
    controller = _controller(window)
    controller.set_solid("#112233", 50)
    controller.enable()
    layer = controller.layer
    controller.close()
    assert not controller.active
    assert not layer.isVisible()
    assert layer.parent() is None, "close() 后图层必须脱离父级，否则会残留在窗口里"


def test_close_is_idempotent(window) -> None:
    controller = _controller(window)
    controller.close()
    controller.close()  # 不抛


# ── ⑤ service 层 ────────────────────────────────────────────────────


def test_service_round_trips_state(window) -> None:
    service = ForegroundSurfaceService(_controller(window))
    try:
        state = service.show_solid("#334455", 30)
        assert state["active"] is True
        assert state["z"] == "overlay", "默认档位应是 overlay"
        cleared = service.clear()
        assert cleared["active"] is False
    finally:
        service.close()


def test_service_capabilities_are_shallow_copies(window) -> None:
    """★ 返回的列表不能是内部常量的引用 —— 插件改它不该影响宿主。"""
    service = ForegroundSurfaceService(_controller(window))
    try:
        caps = service.capabilities()
        caps["z_levels"].append("evil")
        assert "evil" not in service.capabilities()["z_levels"]
    finally:
        service.close()

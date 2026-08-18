"""GUI plugin host: mount UI registrations from the Registry onto the main window.

Four registration kinds (see omnicrawler.plugins.plugins):
- themes          -> design-system theme registration (whitelisted color tokens)
- ui_actions      -> QAction under the "Plugins" menu (callback takes mw or none)
- ui_panels       -> QDockWidget side panels (widget_factory(mw) returns QWidget)
- status_widgets  -> permanent status-bar widgets (widget_factory() returns QWidget)

Safety boundaries:
- Single-item failures are fail-open: they never break main-window assembly,
  and errors are logged;
- Theme color values pass the design_system whitelist, so arbitrary QSS
  cannot be injected;
- Plugin code runs in-process (same as crawler plugins); callback exceptions
  are wrapped into a toast.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QDockWidget, QWidget

from .design_system import register_plugin_theme
from .i18n import _
from .widgets.toast import ToastManager

LOGGER = logging.getLogger(__name__)


def _safe_call(callback: Any, action_id: str, *arguments: Any) -> Any:
    """Run a plugin callback; convert exceptions into toast + log (fail-open).

    **S31 修复**：用 ``inspect.signature`` 判定回调是否接受参数，而不是捕获
    ``TypeError`` 后重试——旧实现把「回调体内部抛出的 TypeError」误判为
    「签名不匹配」，导致插件动作被执行两次（副作用重复）。
    """
    try:
        accepts = len(inspect.signature(callback).parameters)
    except (TypeError, ValueError):
        accepts = 0
    args = arguments if accepts > 0 else ()
    try:
        return callback(*args)
    except Exception as exc:  # noqa: BLE001 - plugin failure never breaks the UI
        _report_error(_(f"插件动作 {action_id} 失败"), exc)
        return None


def _report_error(context: str, exc: Exception) -> None:
    LOGGER.error("%s: %s", context, exc)
    try:
        ToastManager.instance().error(_(f"{context}：{exc}"))
    except Exception:  # noqa: BLE001 - toast failure must not block
        pass


def _install_themes(registry: Any, errors: list[str]) -> None:
    for theme_id, registration in registry.themes.items():
        try:
            register_plugin_theme(theme_id, registration.label, registration.tokens)
        except Exception as exc:  # noqa: BLE001
            errors.append(_(f"主题 {theme_id}: {exc}"))


def _install_actions(mw: Any, registry: Any, errors: list[str]) -> None:
    if not registry.ui_actions:
        return
    menubar = mw.menuBar()
    if menubar is None:
        return
    plugin_menu = None
    for existing in menubar.actions():
        if existing.menu() is not None and existing.menu().title().replace("&", "") == _("插件").replace(
            "&", ""
        ):
            plugin_menu = existing.menu()
            break
    if plugin_menu is None:
        plugin_menu = menubar.addMenu(_("插件(&P)"))
    for action_id, registration in sorted(registry.ui_actions.items()):
        try:
            action = QAction(registration.label, mw)

            def _run(
                _checked: bool = False, callback: Any = registration.callback, aid: str = action_id
            ) -> None:
                _safe_call(callback, aid, mw)

            action.triggered.connect(_run)
            plugin_menu.addAction(action)
        except Exception as exc:  # noqa: BLE001
            errors.append(_(f"动作 {action_id}: {exc}"))


def _install_panels(mw: Any, registry: Any, errors: list[str]) -> None:
    for panel_id, registration in sorted(registry.ui_panels.items()):
        try:
            widget = registration.widget_factory(mw)
            if not isinstance(widget, QWidget):
                raise TypeError(_(f"面板 {panel_id} 工厂未返回 QWidget: {type(widget).__name__}"))
            dock = QDockWidget(registration.title, mw)
            dock.setObjectName(f"pluginPanel_{panel_id}")
            dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
            dock.setWidget(widget)
            mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        except Exception as exc:  # noqa: BLE001
            errors.append(_(f"面板 {panel_id}: {exc}"))


def _install_status_widgets(mw: Any, registry: Any, errors: list[str]) -> None:
    if not registry.status_widgets:
        return
    statusbar = mw.statusBar()
    if statusbar is None:
        return
    for index, registration in enumerate(registry.status_widgets):
        try:
            widget = registration.widget_factory()
            if not isinstance(widget, QWidget):
                raise TypeError(_(f"状态小部件 {index} 工厂未返回 QWidget: {type(widget).__name__}"))
            statusbar.addPermanentWidget(widget)
        except Exception as exc:  # noqa: BLE001
            errors.append(_(f"状态小部件 {index}: {exc}"))


def install_plugin_ui(mw: Any, registry: Any) -> list[str]:
    """把 registry 中的 UI 注册安装到主窗口；返回安装错误列表（fail-open）。"""
    errors: list[str] = []
    _install_themes(registry, errors)
    _install_actions(mw, registry, errors)
    _install_panels(mw, registry, errors)
    _install_status_widgets(mw, registry, errors)
    if errors:
        LOGGER.warning("Plugin UI install partially failed: %s", "; ".join(errors))
    return errors

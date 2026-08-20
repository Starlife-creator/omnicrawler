"""全局快捷键管理器。

将全部 8 个快捷键绑定到 QAction，确保无论当前焦点在哪个控件上，
快捷键都能正确触发对应操作。

快捷键列表（可通过 settings 自定义）：
- Ctrl+S       保存配置
- Ctrl+R       运行任务
- Ctrl+Shift+S 停止任务
- Ctrl+E       切换向导/编辑器
- Ctrl+T       打开模板库
- F5           刷新结果页
- Ctrl+Shift+F 格式化 YAML
- Ctrl+Shift+M 切换请勿打扰模式
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow

from .i18n import _
from .settings import AppSettings


class GlobalShortcutManager(QObject):
    """全局快捷键管理器。

    将快捷键注册为 QMainWindow 的 QAction，设为 ApplicationShortcut 作用域，
    确保在任意子控件获得焦点时也能触发。
    """

    def __init__(self, main_window: QMainWindow) -> None:
        super().__init__(main_window)
        self._main_window = main_window
        self._actions: dict[str, QAction] = {}
        self._callbacks: dict[str, Callable[[], None]] = {}
        self._settings = AppSettings.instance()

    def register_all(self, callbacks: dict[str, Callable[[], None]]) -> None:
        """注册全部 8 个快捷键。

        Args:
            callbacks: 键名到回调函数的映射，键名必须为：
                save, run, stop, toggle_editor, open_templates,
                refresh, format_yaml, toggle_dnd
        """
        shortcuts = self._settings.shortcuts
        labels = {
            "save": _("保存配置"),
            "run": _("运行任务"),
            "stop": _("停止任务"),
            "toggle_editor": _("切换向导/编辑器"),
            "open_templates": _("打开模板库"),
            "refresh": _("刷新结果页"),
            "format_yaml": _("格式化 YAML"),
            "toggle_dnd": _("切换请勿打扰模式"),
        }

        for key, callback in callbacks.items():
            if key not in shortcuts:
                continue
            seq = shortcuts[key]
            action = QAction(labels.get(key, key), self._main_window)
            action.setShortcut(QKeySequence(seq))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(callback)
            self._main_window.addAction(action)
            self._actions[key] = action
            self._callbacks[key] = callback

    def get_action(self, key: str) -> QAction | None:
        """获取指定快捷键的 QAction（用于菜单复用）。"""
        return self._actions.get(key)

    def rebind(self, key: str, new_sequence: str) -> bool:
        """运行时重新绑定快捷键。

        Args:
            key: 快捷键键名。
            new_sequence: 新的快捷键序列字符串。

        Returns:
            是否成功重绑定。
        """
        action = self._actions.get(key)
        if action is None:
            return False
        action.setShortcut(QKeySequence(new_sequence))
        return True

    @property
    def registered_keys(self) -> list[str]:
        """返回已注册的快捷键键名列表。"""
        return list(self._actions.keys())

"""页面骨架：把「继承设计体系」变成默认行为。

**为什么存在**：设计体系的部件（令牌、主题、空态、Toast、无障碍、动效、图标）都已建成，
但 2026-09-11 勘察发现**新页面接不上**——动效与图标注册表 0 个视图接入、`EmptyState`
仅 3/39、`setAccessibleName` 仅 6/39、9 个文件仍在写内联样式。即"资产齐全、继承路径缺失"。
本模块提供那条路径。

**继承后自动获得**：

- 对象名与**无障碍名**（满足 `tools/check_gui_conventions.py` 的约定，新文件零容忍）
- 当前主题令牌 `self.tokens`（颜色不再需要各页面自取）
- **三态状态区**：空 / 加载 / 错误（统一走 `EmptyState`，视觉与主题自动一致）
- 轻提示 `notify()`（统一走 `ToastManager`）
- 布局基线（间距用 `SPACING` 刻度，不写魔法数）

**子类只需实现 `build_ui(container)`**，把原先 `__init__` 里的 UI 搭建搬进去。

**样式约定**：全局 QSS 由 `ThemeManager` 统一应用（见 `gui.delegates.theme`），
页面**默认不要**自设样式；确需局部样式时，用 `self.local_style()` 从令牌生成
（不要写字面量样式串，门禁会拦）。
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..design_system import SPACING, ThemeManager, VisualTokens
from ..i18n import _
from ..widgets.empty_state import EmptyState
from ..widgets.toast import ToastManager

#: 三态默认图标（空 / 加载 / 错误）
_STATE_ICONS: dict[str, str] = {"empty": "📭", "loading": "⏳", "error": "⚠️"}


class BaseView(QWidget):
    """页面基类：内容区 + 状态区，样式与无障碍默认接设计体系。

    用法（**两段式**：先 `super().__init__`，再设自有状态，最后 `finish_setup()`）::

        class MyView(BaseView):
            def __init__(self, parent=None) -> None:
                super().__init__(accessible_name=_("我的页面"), object_name="myView", parent=parent)
                self._data = load_data()      # 自有状态：此时才可设置
                self.finish_setup()           # 触发 build_ui + 局部样式

            def build_ui(self, container: QWidget) -> None:
                layout = QVBoxLayout(container)
                layout.addWidget(QLabel(_("正文")))

    为什么不在 `BaseView.__init__` 里自动调 `build_ui`：那会在 super() 期间执行子类代码，
    而子类属性此刻尚未赋值（经典陷阱）。显式 `finish_setup()` 让顺序可读、可测。

            # 需要时切换状态
            # self.show_empty(_("暂无数据"), _("导入后即可查看"))
            # self.show_loading(_("正在加载…"))
            # self.show_error(_("加载失败"), retry=self.reload)
    """

    def __init__(
        self,
        *,
        accessible_name: str,
        object_name: str = "",
        parent: QWidget | None = None,
        margins: bool | int = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name or type(self).__name__)
        self.setAccessibleName(accessible_name)

        self._root = QVBoxLayout(self)
        inset = SPACING["lg"] if margins is True else (0 if margins is False else int(margins))
        self._root.setContentsMargins(inset, inset, inset, inset)
        self._root.setSpacing(SPACING["md"])

        # 内容容器：子类的 UI 一律挂在这里，状态区切换时整体显隐
        self._content = QWidget(self)
        self._content.setObjectName(f"{self.objectName()}Content")
        self._root.addWidget(self._content)

        self._state_widget: EmptyState | None = None

    @property
    def content(self) -> QWidget:
        """内容容器：子类 UI 的挂载点（`build_ui` 的入参与此一致）。"""
        return self._content

    def finish_setup(self) -> None:
        """子类 `__init__` 末尾调用：搭建 UI 并应用局部样式。"""
        self.build_ui(self._content)
        self.apply_local_style()

    # ------------------------------------------------------------------ 子类钩子

    def build_ui(self, container: QWidget) -> None:
        """子类在此搭建页面内容（替代在 `__init__` 里直接堆控件）。"""

    # ------------------------------------------------------------------ 设计体系接入

    @property
    def tokens(self) -> VisualTokens:
        """当前主题令牌。颜色一律经此获取，不写十六进制。"""
        return ThemeManager.instance().tokens

    def local_style(self) -> str:
        """局部样式：从令牌生成，供确有局部样式需求的页面使用。

        返回空串表示无需局部样式（多数页面如此——全局 QSS 已覆盖）。
        """
        return ""

    def apply_local_style(self) -> None:
        """应用局部样式（仅当 `local_style()` 返回非空时）。"""
        style = self.local_style()
        if style:
            self.setStyleSheet(style)

    # ------------------------------------------------------------------ 三态与提示

    def _ensure_state_widget(self) -> EmptyState:
        if self._state_widget is None:
            self._state_widget = EmptyState("", "", "", self)
            self._state_widget.setObjectName(f"{self.objectName()}State")
            self._root.addWidget(self._state_widget)
        return self._state_widget

    def _show_state(self, kind: str, title: str, description: str = "") -> None:
        state = self._ensure_state_widget()
        state.set_message(_STATE_ICONS.get(kind, ""), title, description)
        self._content.setVisible(False)
        state.setVisible(True)

    def show_empty(self, title: str, description: str = "") -> None:
        """空态：无数据/未开始。"""
        self._show_state("empty", title, description)

    def show_loading(self, title: str = "", description: str = "") -> None:
        """加载态。"""
        self._show_state("loading", title or _("正在加载…"), description)

    def show_error(self, title: str, description: str = "") -> None:
        """错误态：与空态视觉区分，避免"故障伪装成没数据"。"""
        self._show_state("error", title, description)

    def show_content(self) -> None:
        """回到正常内容态（清除状态区）。"""
        if self._state_widget is not None:
            self._state_widget.setVisible(False)
        self._content.setVisible(True)

    def notify(self, message: str, level: str = "info") -> None:
        """轻提示：统一走 ToastManager（level: info / success / warning / error）。"""
        toast = ToastManager.instance()
        method = getattr(toast, level, None)
        if not callable(method):
            method = toast.info
        method(message)

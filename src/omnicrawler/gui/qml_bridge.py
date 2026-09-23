"""Q1 试点：把 QWidget 侧的设计令牌与数据送进 QML 的桥。

两条**实测确立的**约束（见 `.audit-tmp/probe_qml_tokens.py` 的探测结论）
----------------------------------------------------------------------

1. **令牌必须惰性读取**：`ThemeManager.tokens` 在换主题时会被**替换**，
   桥若在构造时捕获对象，QML 会永远显示旧主题的颜色。
2. **属性必须有 ``notify`` 信号**：没有 notify，QML 绑定**永不重算**
   （实测现象：换了主题、令牌值确实变了，QML 渲染仍是旧色）。
   ★ 另外：离屏环境下 `QQuickWidget.grab()` 有渲染缓存，测试**必须先强制重绘**
   （改尺寸或重设 source）再截图，否则会把"没重绘"误判成"绑定失效"。

关于"QML 单例"：`qmlRegisterSingletonInstance` / `qmlRegisterSingletonType`
在本机 PySide6 6.11 上**实测不接受文档签名**（两种参数形式都被拒）。本试点改用
`rootContext().setContextProperty(...)`：语义上对本页面等价（QML 侧同样以
`VisualTokens.xxx` 取值），且不需要在 QML 里 import 版本化模块。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
)

from ..i18n import _

#: 送进 QML 的令牌名 → 用途（**只暴露 QML 真正用到的**，不做全量镜像）
QML_TOKEN_NAMES: tuple[str, ...] = (
    "canvas",
    "surface",
    "elevated",
    "text",
    "muted",
    "border",
    "primary",
    "warning",
)


class TokenBridge(QObject):
    """把 :class:`VisualTokens` 暴露给 QML（活绑定；换主题即刷新）。

    ★ **必须写成类属性**：`Property(...)` 只有作为**类属性**才会被注册进
    Qt 元对象；`setattr(instance, name, Property(...))` 不会被注册 ——
    实测 QML 里 `VisualTokens.canvas` 取到 undefined，根矩形整个变白
    （看起来像"没数据"，实际是"没绑定"）。

    ★ **必须惰性读取**：`ThemeManager.tokens` 在换主题时会被**替换**；
    ★ **必须有 ``notify``**：没有 notify，QML 绑定永不重算。
    """

    tokens_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from .design_system import ThemeManager

        self._manager = ThemeManager.instance()
        self._manager.theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, *_args: object) -> None:
        self.tokens_changed.emit()

    def _read(self, token_name: str) -> str:
        tokens = self._manager.tokens
        if not hasattr(tokens, token_name):
            raise AttributeError(_("未知颜色令牌：{0}").format(token_name))
        return str(getattr(tokens, token_name))

    def _get_canvas(self) -> str:
        return self._read("canvas")

    def _get_surface(self) -> str:
        return self._read("surface")

    def _get_elevated(self) -> str:
        return self._read("elevated")

    def _get_text(self) -> str:
        return self._read("text")

    def _get_muted(self) -> str:
        return self._read("muted")

    def _get_border(self) -> str:
        return self._read("border")

    def _get_primary(self) -> str:
        return self._read("primary")

    def _get_warning(self) -> str:
        return self._read("warning")

    canvas = Property(str, _get_canvas, notify=tokens_changed)
    surface = Property(str, _get_surface, notify=tokens_changed)
    elevated = Property(str, _get_elevated, notify=tokens_changed)
    text = Property(str, _get_text, notify=tokens_changed)
    muted = Property(str, _get_muted, notify=tokens_changed)
    border = Property(str, _get_border, notify=tokens_changed)
    primary = Property(str, _get_primary, notify=tokens_changed)
    warning = Property(str, _get_warning, notify=tokens_changed)

    def disconnect_theme(self) -> None:
        """解除主题信号连接（页面销毁时调用，避免单例信号累积死委托）。"""
        try:
            self._manager.theme_changed.disconnect(self._on_theme_changed)
        except (RuntimeError, TypeError):  # 已断开 / 已销毁
            return


#: B008：函数默认参数里不许做调用 —— 用模块级单例作"无父"哨兵。
_NO_PARENT = QModelIndex()


class ShowcaseModel(QAbstractListModel):
    """市场橱窗条目模型（只读）。

    角色名直接给 QML 用（``model.name`` 等），避免在 QML 里写魔法数字角色。
    """

    #: 角色名 —— 与 QML 里的 ``model.<name>`` 一一对应
    ROLE_NAMES: tuple[str, ...] = ("name", "version", "kinds", "summary")

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[dict[str, str]] = []

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802 - Qt 命名
        base = int(Qt.ItemDataRole.UserRole)
        return {
            base + index + 1: QByteArray(name.encode("utf-8"))
            for index, name in enumerate(self.ROLE_NAMES)
        }

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _NO_PARENT) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._items)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        base = int(Qt.ItemDataRole.UserRole)
        offset = role - base - 1
        if 0 <= offset < len(self.ROLE_NAMES):
            return self._items[index.row()].get(self.ROLE_NAMES[offset], "")
        return None

    def set_items(self, items: list[dict[str, str]]) -> None:
        """整体替换条目（只有内容真的变了才发信号，避免无谓重绘）。"""
        normalized = [
            {role: str(item.get(role, "") or "") for role in self.ROLE_NAMES} for item in items
        ]
        if normalized == self._items:
            return
        self.beginResetModel()
        self._items = normalized
        self.endResetModel()

    @property
    def items(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._items]


class QmlTexts(QObject):
    """页面静态文案（过 i18n，QML 侧不写死中文）。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def _get_title(self) -> str:
        return _("市场橱窗")

    def _get_subtitle(self) -> str:
        return _("本地已安装/自制的市场条目（QML 试点页面，仅用于验证渲染与主题跟随）。")

    def _get_empty_hint(self) -> str:
        return _("还没有可展示的本地条目：把插件目录放入 plugins/ 或 plugins_installed/ 后重新进入本页。")

    title = Property(str, _get_title, constant=True)
    subtitle = Property(str, _get_subtitle, constant=True)
    empty_hint = Property(str, _get_empty_hint, constant=True)

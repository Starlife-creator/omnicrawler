"""Q1 试点页：以 QQuickWidget 嵌入的市场橱窗（《优化方案》§11.3）。

范围与边界
----------

* **只做新增页面**，不动任何存量 QWidget 视图（§11.3）；
* 前置两条**均已实测成立**（探测脚本与结论记在提交信息里）：
  ① 令牌 → QML 渲染连通、且**跟随主题**（换主题后强制重绘即变色）；
  ② QML 离屏可渲染、可截图 ⇒ 有可回归的测试基线。
* 缺 QML 运行时（例如只装了 `PySide6-Essentials`，或冻结包里没收集 Qt QML 插件）时
  **显式降级**：本页显示可行动提示，其它页面不受影响。

★ 关于"扩大 QML 迁移"：§11.3 给的前提是"试点页面实际使用可维护 **且** 出现
「桌面端富 Web 交互」硬需求"，**两者缺一不议**。当前第二个条件不成立
（没有任何已记录的需求指向它），因此本页是**试点**，不构成迁移信号。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..i18n import _

if TYPE_CHECKING:  # 仅为类型标注；避免在导入期拉起 Qt QML
    from ..qml_bridge import ShowcaseModel, TokenBridge

_QML_DIRNAME = "qml"
_PAGE_QML = "ShowcasePage.qml"

_QML_IMPORT_ERROR = ""


def qml_available() -> bool:
    """QML 运行时是否可用（结果缓存；只探测一次）。"""
    global _QML_IMPORT_ERROR
    try:
        from PySide6 import QtQml, QtQuickWidgets  # noqa: F401
    except ImportError as exc:
        _QML_IMPORT_ERROR = str(exc)
        return False
    return True


def qml_unavailable_hint() -> str:
    """缺 QML 运行时时的**可行动**提示。"""
    return _(
        "当前环境缺少 Qt QML 运行时（PySide6 的 QtQml/QtQuickWidgets 组件），"
        "「市场橱窗（QML 试点）」暂不可用。"
        '安装：pip install "PySide6-Addons>=6.5,<7"；'
        "便携包需在打包时一并收集 Qt QML 插件与 qml/ 目录。"
    )


def qml_page_source() -> Path:
    """页面 QML 文件路径（随包分发，见 packaging/*.spec 的 datas）。"""
    return Path(__file__).resolve().parent.parent / _QML_DIRNAME / _PAGE_QML


def local_showcase_items(project_root: Path) -> list[dict[str, str]]:
    """把本地市场条目转成 QML 模型条目（复用既有扫描 API，不另造数据源）。"""
    from .market_home import scan_local_plugins

    items: list[dict[str, str]] = []
    for entry in scan_local_plugins(Path(project_root)):
        kinds = "/".join(getattr(entry, "plugin_types", []) or []) or str(
            getattr(entry, "status", "") or ""
        )
        items.append(
            {
                "name": str(getattr(entry, "name", "") or ""),
                "version": str(getattr(entry, "version", "") or ""),
                "kinds": kinds,
                "summary": str(getattr(entry, "description", "") or ""),
            }
        )
    return items


class QmlShowcaseView(QWidget):
    """市场橱窗（QML 试点）页面。

    ``project_root`` 由主窗口给出，用于取本地市场条目；QML 不可用时本页只显示
    降级提示（不抛错、不影响其它页面）。
    """

    def __init__(self, project_root: Path | str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("qmlShowcase")
        self.setAccessibleName(_("市场橱窗（QML 试点）"))
        self._project_root = Path(project_root)
        self._bridge: TokenBridge | None = None
        self._model: ShowcaseModel | None = None
        self._quick: QWidget | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._hint = QLabel(qml_unavailable_hint())
        self._hint.setWordWrap(True)
        self._hint.setObjectName("muted")
        self._hint.setAccessibleName(_("QML 不可用提示"))
        self._hint.setVisible(False)
        self._layout.addWidget(self._hint)

        self.refresh()

    # ── 对外 ──────────────────────────────────────────────
    def refresh(self) -> None:
        """加载/刷新页面内容（重复调用是幂等的）。"""
        if not qml_available():
            self._teardown_quick()
            self._hint.setVisible(True)
            self.setAccessibleDescription(qml_unavailable_hint())
            return
        self._hint.setVisible(False)
        self._ensure_quick()
        if self._model is not None:
            items = local_showcase_items(self._project_root)
            self._model.set_items(items)
            self.setAccessibleDescription(
                _("QML 试点页面：已展示 {0} 个本地市场条目。").format(len(items))
            )

    @property
    def showcase_model(self) -> object:
        """QML 模型（供测试断言条目）。"""
        return self._model

    @property
    def token_bridge(self) -> object:
        return self._bridge

    def shutdown(self) -> None:
        """释放 QML 资源（页面销毁/窗口关闭时调用）。"""
        if self._bridge is not None:
            self._bridge.disconnect_theme()
        self._teardown_quick()

    # ── 内部 ──────────────────────────────────────────────
    def _ensure_quick(self) -> None:
        if self._quick is not None:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtQuickWidgets import QQuickWidget

        from ..qml_bridge import QmlTexts, ShowcaseModel, TokenBridge

        widget = QQuickWidget(self)
        widget.setObjectName("qmlShowcaseCanvas")
        widget.setAccessibleName(_("市场橱窗内容"))
        widget.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._bridge = TokenBridge(widget)
        self._model = ShowcaseModel(widget)
        texts = QmlTexts(widget)
        # ★ 用 root context 注入：本机 PySide6 6.11 的 qmlRegisterSingleton* 实测不接受
        #   文档签名（见 qml_bridge 模块说明），上下文属性对本页面语义等价。
        context = widget.rootContext()
        context.setContextProperty("VisualTokens", self._bridge)
        context.setContextProperty("ShowcaseModel", self._model)
        context.setContextProperty("I18n", texts)
        widget.setSource(QUrl.fromLocalFile(str(qml_page_source())))
        self._layout.addWidget(widget)
        self._quick = widget

    def _teardown_quick(self) -> None:
        if self._quick is None:
            return
        self._layout.removeWidget(self._quick)
        self._quick.deleteLater()
        self._quick = None
        self._bridge = None
        self._model = None

"""「环境与依赖」面板 —— 功能设置页的原生依赖徽标 + 就地安装入口（决策四 P1）。

与插件市场页的分工：
* **插件市场页（P0）**：只读徽标展示**插件声明依赖**（warning 类，非阻断）；
* **本面板（P1）**：只读徽标展示**原生依赖 / 能力**（error 类，缺失即阻断），
  并给出**就地安装入口**（决策三/八：不要逼用户去命令行 pip install）。

设计边界（决策四 A：打开即检测 = 只读）：
* 打开面板**只探测、只画徽标**，绝不自动安装、绝不弹框；
* 安装按钮是**用户主动点击**才触发的动作，走统一多源回退安装器
  （``services.dependency_installer``），并复用 ``dependency_dialog`` 的失败原因链展示。

数据源：``core.capabilities.capability_report(mode="quick")`` 的分级能力报告
（standard/full/optional），把"未就绪"的原生组件转成可展示行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.background_worker import BackgroundWorker
from ..i18n import _

LOGGER = logging.getLogger(__name__)


# ── 纯逻辑：把能力报告转成可展示 / 可安装的行 ───────────────────────────


@dataclass(frozen=True, slots=True)
class CapabilityRow:
    """一个可展示的能力组件行（原生依赖 / Python 包通吃）。"""

    key: str
    name: str
    tier: str
    ready: bool
    description: str = ""
    #: 可安装需求串（pip 形式）；为空表示"无自动安装路径"（如系统级 tesseract）。
    requirement: str = ""
    #: 依赖类别：native（阻断）或 plugin（非阻断）。
    dependency_class: str = "native"


#: 组件 key → pip 需求串（能自动安装的才登记；系统级组件不放这里）。
_INSTALLABLE_REQUIREMENTS: dict[str, str] = {
    "pdf_text": "pdfplumber",
    "xlsx": "openpyxl",
    "playwright": "playwright",
    "gui": "PySide6",
    "html_parsing": "beautifulsoup4",
    "xml_parsing": "lxml",
    "async_http": "httpx",
    "tls_impersonate": "curl_cffi",
    "websocket": "websockets",
    "selenium": "selenium",
    "paddleocr": "paddleocr",
    "core_yaml": "PyYAML",
}


def capability_rows(report: dict[str, Any]) -> list[CapabilityRow]:
    """把 ``capability_report`` 的分级结果摊平成行（stable 顺序：standard→full→optional）。"""
    rows: list[CapabilityRow] = []
    for tier in ("standard", "full", "optional"):
        section = report.get(tier) or {}
        items = section.get("items") or {}
        for key, item in items.items():
            if not isinstance(item, dict):
                continue
            rows.append(
                CapabilityRow(
                    key=str(key),
                    name=str(item.get("name") or key),
                    tier=str(item.get("tier") or tier),
                    ready=bool(item.get("ready")),
                    description=str(item.get("description") or ""),
                    requirement=_INSTALLABLE_REQUIREMENTS.get(str(key), ""),
                )
            )
    return rows


def missing_installable_rows(rows: list[CapabilityRow]) -> list[CapabilityRow]:
    """未就绪 **且** 有自动安装路径的行（面板的"就地安装"入口只列这些）。"""
    return [row for row in rows if not row.ready and row.requirement]


def readiness_badge(row: CapabilityRow) -> str:
    """行的只读状态徽标（打开即检测：只展示，不动作）。"""
    if row.ready:
        return _("[就绪]")
    if not row.requirement:
        return _("[缺失·需手动]")
    return _("[缺失·可安装]")


# ── GUI：环境与依赖面板 ──────────────────────────────────────────────


class _CapabilityScanWorker(BackgroundWorker):
    """后台跑能力探测（quick 模式；不导入重型可选模块，避免卡界面）。"""

    def work(self) -> dict[str, Any]:
        from ...core.capabilities import capability_report

        return capability_report(mode="quick", require_features=("core", "web", "pdf", "browser", "gui"))


class DependencyCenterDialog(QDialog):
    """「环境与依赖」对话框 —— 只读徽标 + 就地安装入口。

    打开即探测（后台），不自动装、不弹框；用户点某一行的「安装」才动作。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        registry: Any = None,
        on_installed: Callable[[], object] | None = None,
        config_raw: dict[str, Any] | None = None,
        persist_patch: Callable[[dict[str, Any]], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("环境与依赖"))
        self.setAccessibleName(_("环境与依赖"))
        self.setMinimumSize(560, 480)
        self._registry = registry
        self._on_installed = on_installed
        self._config_raw = config_raw if config_raw is not None else {}
        self._persist_patch = persist_patch
        self._scan_worker: _CapabilityScanWorker | None = None
        #: 存活中的后台线程（扫描 + 安装）；关闭对话框时必须 join，否则线程仍在
        #: 运行时销毁 QApplication 会 abort（实测：非确定性崩溃，破坏后续测试/退出）。
        self._active_workers: list[BackgroundWorker] = []

        root = QVBoxLayout(self)
        header = QLabel(_("本机运行组件与依赖（只读检测）。缺失项可在此就地安装。"))
        header.setWordWrap(True)
        header.setObjectName("dependencyCenterHeader")
        root.addWidget(header)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(6)
        self._body_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._body)
        root.addWidget(scroll, 1)

        button_row = QHBoxLayout()
        self._recheck_btn = QPushButton(_("重新检测"))
        self._recheck_btn.clicked.connect(self.reload)
        button_row.addWidget(self._recheck_btn)
        button_row.addStretch(1)
        close_btn = QPushButton(_("关闭"))
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        root.addLayout(button_row)

        self._status = QLabel(_("正在检测…"))
        self._status.setObjectName("mutedLabel")
        root.addWidget(self._status)

        self.reload()

    # ---- 探测 ----

    def reload(self) -> None:
        self._status.setText(_("正在检测…"))
        worker = _CapabilityScanWorker(parent=self)
        worker.succeeded.connect(self._on_scanned)
        worker.failed.connect(lambda error: self._status.setText(_("检测失败：") + error))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: self._forget_worker(w))
        self._scan_worker = worker
        self._active_workers.append(worker)
        worker.start()

    def _forget_worker(self, worker: BackgroundWorker) -> None:
        self._active_workers = [w for w in self._active_workers if w is not worker]

    def _stop_workers(self) -> None:
        """请求中断并等待全部后台线程结束（对话框关闭 / 销毁前的必要收尾）。

        QThread 仍在运行时销毁 QApplication 会 abort；等待上限 5s 保证界面不卡死
        （扫描/安装是短任务，正常远低于此）。
        """
        for worker in list(self._active_workers):
            try:
                worker.requestInterruption()
                worker.wait(5000)
            except RuntimeError:
                # Qt 侧对象可能已销毁；无可 join 的对象，忽略
                pass
        self._active_workers = []

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        self._stop_workers()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._stop_workers()
        super().done(result)

    def _on_scanned(self, report: dict[str, Any]) -> None:
        rows = capability_rows(report)
        self._render(rows)
        missing = [row for row in rows if not row.ready]
        self._status.setText(
            _("全部就绪。") if not missing else _("有 {0} 项未就绪。").format(len(missing))
        )

    # ---- 渲染 ----

    def _render(self, rows: list[CapabilityRow]) -> None:
        # 清空旧内容（保留末尾 stretch）
        while self._body_layout.count() > 1:
            item = self._body_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for row in rows:
            self._body_layout.insertWidget(self._body_layout.count() - 1, self._row_widget(row))

    def _row_widget(self, row: CapabilityRow) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 4, 6, 4)
        label = QLabel(
            _("{0} {1} · {2}").format(readiness_badge(row), row.name, row.description)
        )
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        if not row.ready and row.requirement:
            install_btn = QPushButton(_("安装"))
            install_btn.clicked.connect(lambda _=False, r=row: self._install(r))
            layout.addWidget(install_btn)
        container.setProperty("dependencyRow", True)
        container.setToolTip(row.requirement or row.description)
        return container

    # ---- 就地安装（用户主动触发）----

    def _install(self, row: CapabilityRow) -> None:
        """就地安装一行（用户主动点击）：走统一流程，官方源失败时**轮到镜像源**再弹提示。

        注意 ``install_with_mirror_offer`` 自己就会创建后台安装线程并用事件循环等待
        （内部已保证界面不冻结），因此这里**必须直接在 GUI 线程调用**，不能再套一层
        worker —— 否则会在子线程里创建 QThread 并从子线程弹 QMessageBox（Qt 非法）。
        """
        from .dependency_dialog import install_with_mirror_offer

        self._status.setText(_("正在安装 {0}…").format(row.requirement))
        parent = self.parentWidget() or self
        try:
            ok, _enabled = install_with_mirror_offer(
                parent,
                row.requirement,
                registry=self._registry,
                config_raw=self._config_raw,
                persist_patch=self._persist_patch,
            )
        except Exception as exc:  # noqa: BLE001 - 单行安装异常不该让整个面板崩掉
            LOGGER.warning("安装 %s 异常：%s", row.requirement, exc)
            ok = False
        if ok:
            if self._on_installed is not None:
                self._on_installed()
            self.reload()
        else:
            self._status.setText(_("安装失败：") + row.requirement)


def open_dependency_center(
    parent: QWidget | None,
    *,
    registry: Any = None,
    on_installed: Callable[[], object] | None = None,
    config_raw: dict[str, Any] | None = None,
    persist_patch: Callable[[dict[str, Any]], object] | None = None,
) -> DependencyCenterDialog:
    """便捷入口：构造并**非模态**显示面板（打开即检测，不阻塞主界面）。"""
    dialog = DependencyCenterDialog(
        parent,
        registry=registry,
        on_installed=on_installed,
        config_raw=config_raw,
        persist_patch=persist_patch,
    )
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dialog.show()
    return dialog

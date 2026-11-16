"""组件管理面板（条目 10）：把散落两处的组件入口合并成一个可复用控件。

为什么需要它
------------
组件相关操作此前散落在两个地方，用户找不到：

* 「任务工具 → 可选组件」页（`task_tools.py`）：有 `components:inspect` /
  `import` / `list` / `uninstall` / `rollback` 五个动作，但藏在「已保存任务的
  工具对话框」里——没打开任务的用户根本碰不到；
* 「环境与依赖」面板（`dependency_center.py`）：只有**原生依赖/能力**的只读徽标
  与 pip 安装入口，与组件（`.ocp`）是两套东西。

本模块把「组件的检测 + 检查 + 安装 + 卸载 + 回滚」收进一个控件，
供插件市场的「组件」标签页直接挂载。**这是合并，不是新建能力**——
所有动作最终都调既有的 `services.component_tools.execute()`。

设计约束
--------
* **打开即检测 = 只读**：挂载时只读注册表并画列表，绝不自动安装、绝不弹框；
* 写操作一律要用户显式点击，并在执行前二次确认（与既有 `_manage_component`
  的确认语义一致）；
* 容器里**不显示"空的下载列表"**：组件目录尚未建立时，只展示
  「已安装 + 从本地文件导入」，不画一个空的可下载列表（那比没有更糟）。

前置阻塞（不是本模块能解决的）
------------------------------
`component_trust.pub.pem` 目前不存在（`configs/` 只有 plugin / update 两把钥匙），
所以 `inspect_package` 会报「组件包没有可用的受信签名密钥」。
本模块不绕过该检查——没有信任根就是不能装，这是设计意图。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.background_worker import BackgroundWorker
from ..core.worker_owned_widget import WorkerOwnedWidget
from ..i18n import _

_INTRO = _(
    "可选组件是**离线导入**的受信包（.ocp）：安装前会校验签名与每个文件的哈希，"
    "运行时再次校验。组件可以自带运行时，因此不依赖本机已装的 Python 依赖。"
)


class _ComponentWorker(BackgroundWorker):
    """在后台线程执行组件动作，避免读注册表 / 校验大包时卡住界面。"""

    def __init__(self, operation: str, arguments: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._operation = operation
        self._arguments = arguments

    def work(self) -> Any:  # pragma: no cover - 由线程执行
        from ...services.component_tools import execute

        return execute(self._operation, self._arguments)


class ComponentManagerPanel(WorkerOwnedWidget):
    """组件管理面板：只读检测 + 显式写操作。

    继承 ``WorkerOwnedWidget`` 以复用仓库既有的线程生命周期机制：
    关闭/删除时请求中断并等 ``finished``，**不阻塞 GUI 线程**。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("组件管理"))
        self._worker: _ComponentWorker | None = None
        self._preview: dict[str, Any] = {}
        self._registry_sha = ""
        self._uninstalled = ""
        self._recovery_sha = ""
        self._build_ui()
        self._loaded_once = False
        # **不在构造时启动线程**：面板可能被创建后就一直不可见（例如市场页
        # 从未打开），启动线程会让对象销毁时撞上
        # "QThread: Destroyed while thread is still running"。
        # 改为首次 showEvent 时才做只读检测（见 showEvent）。
        self._set_write_enabled()
        self._status.setText(_("打开组件页时自动读取已安装组件。"))

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        """首次可见时才做只读检测（打开即检测，但不提前占线程）。"""
        super().showEvent(event)
        if not self._loaded_once:
            self._loaded_once = True
            self.refresh()

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        intro = QLabel(_INTRO)
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        self._package = QLabel(_("未选择组件包"))
        self._package.setWordWrap(True)
        self._choose = QPushButton(_("选择离线组件包…"))
        self._choose.clicked.connect(self._pick_package)
        row.addWidget(self._choose)
        row.addWidget(self._package, 1)
        root.addLayout(row)

        self._status = QLabel(_("正在读取组件注册表…"))
        self._status.setWordWrap(True)
        self._status.setAccessibleName(_("组件状态"))
        root.addWidget(self._status)

        actions = QHBoxLayout()
        self._inspect_btn = QPushButton(_("检查所选包"))
        self._inspect_btn.clicked.connect(lambda: self._run("inspect"))
        self._import_btn = QPushButton(_("安装已检查的组件"))
        self._import_btn.clicked.connect(lambda: self._run("import"))
        self._reload_btn = QPushButton(_("刷新已安装列表"))
        self._reload_btn.clicked.connect(self.refresh)
        for button in (self._inspect_btn, self._import_btn, self._reload_btn):
            actions.addWidget(button)
        root.addLayout(actions)

        self._list = QListWidget()
        self._list.setAccessibleName(_("已安装的可选组件"))
        self._list.currentItemChanged.connect(self._show_selected)
        root.addWidget(self._list, 1)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setAccessibleName(_("组件用途、兼容与卸载影响"))
        root.addWidget(self._details, 1)

        write_actions = QHBoxLayout()
        self._uninstall_btn = QPushButton(_("卸载所选组件"))
        self._uninstall_btn.clicked.connect(lambda: self._run("uninstall"))
        self._rollback_btn = QPushButton(_("恢复本次卸载"))
        self._rollback_btn.clicked.connect(lambda: self._run("rollback"))
        write_actions.addWidget(self._uninstall_btn)
        write_actions.addWidget(self._rollback_btn)
        root.addLayout(write_actions)

        self._set_write_enabled(False)

    def _set_write_enabled(self, _enabled: bool = True) -> None:
        """按**当前真实前置条件**刷新按钮可用性。

        不用一个布尔开关统一控制：三个写操作的前置条件不同
        （导入要"已检查且兼容"，卸载要"有注册表 + 选中行"，回滚要"本会话卸载过"）。
        用单一开关会让用户点了才发现条件不满足。
        """
        self._import_btn.setEnabled(bool(self._preview.get("compatible")
                                        and self._preview.get("package_sha256")))
        self._uninstall_btn.setEnabled(
            bool(self._registry_sha) and self._list.currentItem() is not None
        )
        self._rollback_btn.setEnabled(bool(self._uninstalled and self._recovery_sha))

    def _pick_package(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, _("选择受信组件包"), "", _("组件包 (*.ocp *.zip);;所有文件 (*)")
        )
        if not path:
            return
        self._preview.clear()
        self._path = Path(path)
        self._package.setText(self._path.name)
        self._status.setText(_("已选择 {0}；点「检查所选包」验证签名与兼容性。").format(self._path.name))
        self._set_write_enabled(False)

    # ── 数据 ──────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """只读刷新：列出已安装组件。**不触发任何写操作。**"""
        self._run_worker("list", {})

    def _run(self, operation: str) -> None:
        """写操作：先校验前置条件 + 二次确认，再执行。"""
        if operation == "inspect":
            path = getattr(self, "_path", None)
            if path is None:
                self._status.setText(_("请先选择一个离线组件包。"))
                return
            self._run_worker("inspect", {"package": str(path)})
            return

        if operation == "import":
            preview = self._preview
            if not preview.get("compatible") or not preview.get("package_sha256"):
                self._status.setText(_("请先检查所选包，解决签名、依赖与兼容问题后再安装。"))
                return
            arguments = {
                "package": str(getattr(self, "_path", "")),
                "package_sha256": preview["package_sha256"],
                "registry_sha256": preview["registry_sha256"],
                "confirmed": True,
            }
            detail = _describe(preview.get("component") or {})
        elif operation == "uninstall":
            item = self._list.currentItem()
            if item is None or not self._registry_sha:
                self._status.setText(_("请先刷新并选择需要卸载的组件。"))
                return
            info = item.data(Qt.ItemDataRole.UserRole) or {}
            arguments = {
                "name": info.get("name", ""),
                "registry_sha256": self._registry_sha,
                "confirmed": True,
            }
            detail = _describe(info)
        else:  # rollback
            if not self._uninstalled or not self._recovery_sha:
                self._status.setText(_("本会话没有可恢复的卸载。"))
                return
            arguments = {
                "name": self._uninstalled,
                "registry_sha256": self._recovery_sha,
                "confirmed": True,
            }
            detail = _("恢复刚刚卸载的保留版本：{0}").format(self._uninstalled)

        if QMessageBox.question(
            self, _("确认组件操作"), detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._run_worker(operation, arguments)

    def _run_worker(self, operation: str, arguments: dict[str, Any]) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._status.setText(_("上一个组件操作还未完成，请稍候。"))
            return
        self._status.setText(_("正在执行组件操作：{0}…").format(operation))
        for button in (self._inspect_btn, self._import_btn, self._reload_btn,
                       self._uninstall_btn, self._rollback_btn):
            button.setEnabled(False)
        worker = _ComponentWorker(operation, arguments, parent=self)
        worker.succeeded.connect(lambda result: self._apply(operation, result))
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._reset_buttons)
        self._worker = worker
        worker.start()

    def _reset_buttons(self) -> None:
        """终态后复位按钮（成功/失败/取消三条路径都会到这里）。"""
        self._worker = None
        for button in (self._inspect_btn, self._import_btn, self._reload_btn,
                       self._uninstall_btn, self._rollback_btn):
            button.setEnabled(True)
        self._set_write_enabled(True)

    def _on_failed(self, message: str) -> None:
        self._status.setText(_("组件操作失败：{0}").format(message))

    def _apply(self, operation: str, result: dict[str, Any]) -> None:
        if operation == "list":
            self._render_list(result)
        elif operation == "inspect":
            self._preview = dict(result)
            self._status.setText(
                _("签名受信；兼容性：{0}").format(
                    _("通过") if result.get("compatible") else (result.get("compatibility_reason") or _("不兼容"))
                )
            )
            self._details.setPlainText(_describe(result.get("component") or {}))
            self._set_write_enabled(True)
        elif operation == "import":
            self._status.setText(_("已安装组件：{0}").format(
                (result.get("component") or {}).get("name", "")))
            self._preview.clear()
            self.refresh()
        elif operation == "uninstall":
            info = result.get("component") or {}
            self._uninstalled = str(info.get("name", ""))
            self._recovery_sha = str(result.get("registry_sha256", ""))
            self._status.setText(_("已卸载；本会话内可点「恢复本次卸载」回滚。"))
            self.refresh()
        else:  # rollback
            self._uninstalled = ""
            self._recovery_sha = ""
            self._status.setText(_("已恢复。"))
            self.refresh()

    def _render_list(self, result: dict[str, Any]) -> None:
        self._registry_sha = str(result.get("registry_sha256", ""))
        rows = result.get("components") or []
        self._list.clear()
        for info in rows:
            label = f"{info.get('name', '')} {info.get('version', '')}".strip()
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, info)
            self._list.addItem(item)
        if rows:
            self._status.setText(_("已安装 {0} 个组件。").format(len(rows)))
        else:
            self._status.setText(_("尚未安装任何可选组件。"))
        self._set_write_enabled()

    def _show_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        info = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if isinstance(info, dict):
            self._details.setPlainText(_describe(info))
        self._set_write_enabled()

    def shutdown(self) -> None:
        """停止在途的组件操作（复用 ``WorkerOwnedWidget`` 的线程生命周期）。

        **不要**阻塞式等待：基类通过 ``finished`` 回调延后关闭，
        在 GUI 线程上 ``wait()`` 会冻住界面（这正是基类存在的理由）。
        """
        super().shutdown()


def _describe(info: dict[str, Any]) -> str:
    """组件的可读描述（用途 / 体积 / 兼容 / 卸载影响）。"""
    return _(
        "组件：{0} {1}\n用途：{2}\n磁盘需求（字节）：{3}\n核心版本要求：{4}\n"
        "系统：{5}\n架构：{6}\n依赖：{7}\n卸载影响：{8}"
    ).format(
        info.get("name", ""), info.get("version", ""), info.get("purpose", ""),
        info.get("disk_bytes", 0), info.get("core_version") or _("未限定"),
        ", ".join(info.get("platforms") or []) or _("未限定"),
        ", ".join(info.get("architectures") or []) or _("未限定"),
        ", ".join(info.get("dependencies") or []) or _("无"),
        info.get("uninstall_impact", _("恢复时使用保留版本")),
    )


def describe(info: dict[str, Any]) -> str:
    """供测试与外部复用的纯函数版本。"""
    return _describe(info)


def dumps(info: dict[str, Any]) -> str:
    """把组件信息转成稳定的 JSON 文本（便于诊断与测试比对）。"""
    return json.dumps(info, ensure_ascii=False, sort_keys=True, indent=2)

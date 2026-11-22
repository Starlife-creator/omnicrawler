
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
供 OmniCrawler 市场的「组件」标签页直接挂载。**这是合并，不是新建能力**——
所有动作最终都调既有的 `services.component_tools.execute()`。

设计约束
--------
* **打开即检测 = 只读**：挂载时只读注册表并画列表，绝不自动安装、绝不弹框；
* 写操作一律要用户显式点击，并在执行前二次确认（与既有 `_manage_component`
  的确认语义一致）；
* 容器里**不显示"空的下载列表"**：组件目录尚未建立时，只展示
  「已安装 + 从本地文件导入」，不画一个空的可下载列表（那比没有更糟）。

两条安装通道
------------
1. **市场通道（recipe 装配）**：从已验签的 catalog 取组件条目，按 recipe 声明的
   上游 URL 下载并在本地组装。市场只托管几十 KB 的声明，**不托管字节**——
   见 `services/component_recipe.py` 的设计说明。
2. **离线通道（整包 .ocp）**：用户手工下载的受信包，安装前校验签名与每个文件
   的哈希，运行时再次校验。

前一条是本模块新增的（2026-11-18）；后一条是既有能力，保留不变。

历史说明（已不成立）
--------------------
早期版本这里记着「`component_trust.pub.pem` 不存在，`inspect_package` 会报
『组件包没有可用的受信签名密钥』」。**该阻塞已解除**：信任根现已存在
（`configs/component_trust.pub.pem`），离线通道可正常验签。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
from .browser_kernels import BrowserKernelPanel

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

    #: 用户在「浏览器内核」里切换了内核族（空串＝auto）。
    kernel_selected = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        catalog_url: str = "",
        base: Path | None = None,
        trust_source: str = "",
        app_config: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("组件管理"))
        self._worker: _ComponentWorker | None = None
        self._preview: dict[str, Any] = {}
        self._registry_sha = ""
        self._uninstalled = ""
        self._recovery_sha = ""
        # 市场上下文：有 catalog_url 才显示「可下载」区；否则只显示已安装 + 离线导入。
        self._catalog_url = str(catalog_url or "")
        self._base = base
        self._trust_source = str(trust_source or "")
        # 多源聚合需要 AppConfig 才能读到用户配置的市场源列表。
        self._app_config = app_config
        self._entries: list[Any] = []
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

        # ── 可下载（市场）────────────────────────────────────────────────
        # 与插件/模板页同形态：从已验签的 catalog 取条目。
        # **组件条目不含字节**，只声明"从哪拉、多大、什么哈希"——
        # 所以这里展示的是上游域名与下载量，不是包签名。
        if self._catalog_url:
            self._market_label = QLabel(_("可下载组件（来自 OmniCrawler 市场）"))
            self._market_label.setAccessibleName(_("可下载组件"))
            root.addWidget(self._market_label)

            # 与插件/模板页对齐：组件也要能搜索（按 id/名称/发布者/上游域名）。
            self._market_search = QLineEdit()
            self._market_search.setPlaceholderText(_("按名称、ID、发布者或上游域名搜索…"))
            self._market_search.setClearButtonEnabled(True)
            self._market_search.textChanged.connect(self._reload_catalog_filtered)
            root.addWidget(self._market_search)

            self._market_list = QListWidget()
            self._market_list.setAccessibleName(_("可下载组件列表"))
            self._market_list.currentItemChanged.connect(self._show_market_selected)
            self._market_list.setMaximumHeight(160)
            root.addWidget(self._market_list)

            market_row = QHBoxLayout()
            self._market_refresh = QPushButton(_("刷新市场组件列表"))
            self._market_refresh.clicked.connect(self._reload_catalog)
            self._market_install = QPushButton(_("下载并安装所选组件"))
            self._market_install.clicked.connect(self._install_from_market)
            self._market_status = QLabel(_("尚未拉取市场目录。"))
            self._market_status.setWordWrap(True)
            market_row.addWidget(self._market_refresh)
            market_row.addWidget(self._market_install)
            market_row.addWidget(self._market_status, 1)
            root.addLayout(market_row)
        else:
            # 没有 catalog 上下文时不画空列表——那比没有更糟。
            # mypy 需要显式标注：这两条分支给同一个属性赋不同类型。
            self._market_search = None  # type: ignore[assignment]
            self._market_list = None  # type: ignore[assignment]
            self._market_install = None  # type: ignore[assignment]
            self._market_status = None  # type: ignore[assignment]
            self._market_refresh = None  # type: ignore[assignment]

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
        # 与插件/模板页对齐：三分支都应有「校验已安装」。
        self._verify_btn = QPushButton(_("校验所选组件"))
        self._verify_btn.clicked.connect(lambda: self._run("verify"))
        for button in (self._inspect_btn, self._import_btn, self._reload_btn, self._verify_btn):
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

    # ── 市场（可下载组件）────────────────────────────────────────────────
        # ── 浏览器内核（§15.16.7）────────────────────────────────────────
        # 内核本身就是一个组件（`kind: native`），所以放在组件页而不是
        # 另开一个页面 —— 用户找「能装什么」时不该记住两个入口。
        self._kernel_label = QLabel(_("浏览器内核"))
        self._kernel_label.setAccessibleName(_("浏览器内核"))
        root.addWidget(self._kernel_label)
        self._kernel_panel = BrowserKernelPanel(self)
        # 把面板的选择意图向上转发：保存配置是主窗口/市场页的职责，
        # 组件面板只做中转（它不知道 config_path 与 dirty 标记）。
        self._kernel_panel.kernel_selected.connect(self.kernel_selected.emit)
        root.addWidget(self._kernel_panel, 1)

    def _reload_catalog(self) -> None:
        """拉取并聚合全部市场源，填充可下载列表。

        **只读**：不下载任何构件。真正的下载在用户点「下载并安装」并确认后。

        **多源聚合**（2026-11-19）：与插件栏同语义——逐源独立验签、
        失败可见、全部失败才算整体失败。此前组件栏只读单源，
        用户配置了镜像源也看不到镜像里的组件。
        """
        if self._market_list is None or not self._catalog_url:
            return
        from .market_aggregation import (
            MarketAggregationError,
            aggregate_market_catalogs,
            failed_sources,
            freshness_summary,
            source_summary,
        )

        self._market_status.setText(_("正在拉取市场目录…"))
        try:
            aggregated = aggregate_market_catalogs(
                self._app_config,
                trust_source=self._trust_source,
                local_fallback=self._base.parent / "OmniCrawler-market" if self._base else None,
                egress=_market_egress(self._base),
            )
        except MarketAggregationError as exc:
            # 失败归因决定"要不要提示换镜像"：
            # 只有**网络类**失败才建议换源；签名/哈希/ABI 这类安全失败
            # 一律阻断且绝不诱导换源（换源只会绕过校验）。
            message = _("无法拉取市场目录：") + str(exc)
            if _failures_allow_mirror_hint(exc):
                message += "\n" + _(
                    "当前源连接失败，可以尝试在「源与下载镜像设置」里切换镜像源重试。"
                )
            self._market_status.setText(message)
            self._market_list.clear()
            self._entries = []
            return
        except Exception as exc:  # noqa: BLE001 - 市场不可用不该让面板崩
            # 明确区分"拉不到"与"验签失败"：后者是安全问题，必须让用户看见。
            kind = _("目录验签失败，已停止") if isinstance(exc, PermissionError) else _("无法拉取市场目录")
            self._market_status.setText(f"{kind}：{exc}")
            self._market_list.clear()
            self._entries = []
            return

        from .component_market_logic import components_from_catalog

        self._entries = components_from_catalog(aggregated)
        self._market_list.clear()
        source_note = source_summary(aggregated)
        stale_note = freshness_summary(aggregated)
        if not self._entries:
            detail = "（" + source_note + "）" if source_note else ""
            if stale_note:
                detail += _("；镜像索引滞后：") + stale_note
            self._market_status.setText(_("市场暂无组件。") + detail)
            return
        broken = failed_sources(aggregated)
        for entry in self._entries:
            item = QListWidgetItem(f"{entry.name} {entry.version}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self._market_list.addItem(item)
        note = _("共 {n} 个组件").format(n=len(self._entries))
        if source_note:
            note += f"（{source_note}）"
        if broken:
            # 失败源必须可见——否则用户以为"就这么多"
            note += _("；{n} 个源不可用").format(n=len(broken))
        if stale_note:
            # 索引新鲜度：镜像有同步周期，用户需要知道"没有某条目"是不是因为滞后
            note += _("；索引滞后：") + stale_note
        self._market_status.setText(note + _("。选中可查看上游域名与下载量。"))

    def _reload_catalog_filtered(self) -> None:
        """按当前搜索词重画列表（数据已在 `self._entries`，不重新拉目录）。"""
        if self._market_list is None:
            return
        from .component_market_logic import filter_components

        query = self._market_search.text() if self._market_search is not None else ""
        visible = filter_components(self._entries, query)
        self._market_list.blockSignals(True)
        self._market_list.clear()
        for entry in visible:
            item = QListWidgetItem(f"{entry.name} {entry.version}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            self._market_list.addItem(item)
        self._market_list.blockSignals(False)
        if self._market_status is not None:
            total = len(self._entries)
            if query.strip():
                self._market_status.setText(
                    _("匹配 {n} / {total} 个组件。").format(n=len(visible), total=total)
                )
            else:
                self._market_status.setText(
                    _("共 {n} 个组件。选中可查看上游域名与下载量。").format(n=total)
                )

    def _selected_entry(self) -> Any:
        if self._market_list is None:
            return None
        item = self._market_list.currentItem()
        if item is None:
            return None
        component_id = item.data(Qt.ItemDataRole.UserRole)
        for entry in self._entries:
            if entry.id == component_id:
                return entry
        return None

    def _show_market_selected(self) -> None:
        """选中组件时展示**下载前须知**：上游域名、大小、阻断项。"""
        entry = self._selected_entry()
        if entry is None or self._market_status is None:
            return
        from .component_market_logic import human_bytes, pre_install_checks

        blockers, warnings = pre_install_checks(entry)
        parts = [f"{human_bytes(entry.declared_total_bytes)} · {', '.join(entry.hosts) or _('无上游')}"]
        if warnings:
            parts.append("；".join(warnings))
        if blockers:
            parts.append(_("无法安装：") + "；".join(blockers))
        self._market_status.setText(" — ".join(parts))
        if self._market_install is not None:
            # 有阻断项就禁用按钮，而不是让用户点了才失败。
            self._market_install.setEnabled(not blockers)

    def _install_from_market(self) -> None:
        """下载并安装所选组件（走 recipe 装配）。"""
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, _("未选择组件"), _("请先在列表中选择一个组件。"))
            return
        from .component_market_logic import describe_for_confirm, pre_install_checks

        blockers, _warnings = pre_install_checks(entry)
        if blockers:
            QMessageBox.warning(
                self, _("无法安装"), "\n".join(blockers)
            )
            return

        reply = QMessageBox.question(
            self,
            _("确认下载并安装"),
            describe_for_confirm(entry),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        recipe_rel = entry.recipe_file
        if not recipe_rel:
            QMessageBox.warning(self, _("无法安装"), _("条目缺少 recipe 文件路径。"))
            return
        if self._base is None:
            QMessageBox.warning(self, _("无法安装"), _("缺少项目根目录，无法定位 recipe。"))
            return

        recipe_path = self._base.parent / "OmniCrawler-market" / recipe_rel
        if not recipe_path.is_file():
            QMessageBox.warning(
                self, _("无法安装"), _("找不到本地 recipe 文件：{p}").format(p=recipe_path)
            )
            return

        from ...services.component_install import RecipeInstallError, install_recipe_file

        self._market_status.setText(_("正在从上游下载并装配…"))
        try:
            summary = install_recipe_file(
                recipe_path,
                self._base / "components",
                egress=_market_egress(self._base),
            )
        except RecipeInstallError as exc:
            self._market_status.setText(_("安装失败：") + str(exc))
            QMessageBox.warning(self, _("安装失败"), str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._market_status.setText(_("安装失败：") + str(exc))
            QMessageBox.warning(self, _("安装失败"), str(exc))
            return

        self._market_status.setText(
            _("已安装：{path}").format(path=summary.get("path", "?"))
        )
        self.refresh()


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


def _failures_allow_mirror_hint(exc: Any) -> bool:
    """聚合全失败时，判断是否可以向用户建议"换个镜像重试"。

    **只认网络类失败**。签名校验失败、哈希不符、`sequence` 回退、ABI 不兼容
    这类安全失败返回 False —— 换镜像不会让一个被篡改的包变合法，
    提示用户换源等于诱导他绕过校验。

    源报告里的 `error` 是文本，这里按 `mirror_failure_policy` 的关键词判据
    归类；无法归类的一律当作"不建议换源"（默认保守）。
    """
    from ...services.mirror_failure_policy import should_suggest_mirror

    reports = getattr(exc, "sources", None)
    if not reports:
        return False

    network_like = False
    for item in reports:
        error_text = str(item.get("error") or "")
        if not error_text:
            continue
        # 用伪异常承载文本，复用同一套关键词判据
        probe = RuntimeError(error_text)
        if not should_suggest_mirror(probe):
            # 只要有一条不是网络类（即安全/配置类），整体就不建议换源
            return False
        network_like = True
    return network_like


def _market_egress(base: Any) -> Any:
    """构造受控出站（与插件/模板市场同一套：限制私网 + **强制审计**）。"""
    import copy

    from ...core.config import DEFAULTS, AppConfig
    from ...security.egress import EgressBroker

    root = Path(base) if base is not None else Path.cwd()
    raw = copy.deepcopy(dict(DEFAULTS))
    egress_cfg = raw.setdefault("egress", {})
    if isinstance(egress_cfg, dict):
        egress_cfg["audit"] = True
    raw.setdefault("project", {"name": "component-market", "workspace": str(root)})
    return EgressBroker(AppConfig(root, root, raw, root))

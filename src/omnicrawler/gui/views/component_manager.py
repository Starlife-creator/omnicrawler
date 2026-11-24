
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
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
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
        #: 已安装组件：name -> info（供列表打 ✓ 与卸载判据）。
        self._installed: dict[str, dict[str, Any]] = {}
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
        """按**插件页同形态**重建：顶部状态栏 + 左列表 / 右详情 + 底部状态。

        为什么重写（2026-11-24，用户实测反馈「组件市场还是没优化得和插件一样好」）：

        此前是**竖排 11 块**：简介 / 可下载列表 / 刷新行 / 选包行 / 状态 / 4 个按钮 /
        已安装列表 / 详情框 / 2 个按钮 / 内核面板。用户要滚很长，且「可下载」与
        「已安装」两套东西混在一条竖线上，分不清哪些按钮作用于哪一份列表。

        插件页（`market.py:275`）是**左右分栏**：左边是「能装什么」，右边是选中项
        的详情与操作按钮。两边职责清楚，用户不会点错。这里照它做。

        三处刻意保留的差异（不是没对齐）：

        * 组件页多一个「浏览器内核」子面板 —— 内核本身就是 `kind: native` 的组件，
          放进同一页比另开入口好（用户找「能装什么」时不该记两个地方）；
        * 离线 `.ocp` 导入通道是组件**独有**的，放在右侧详情区底部；
        * 已安装列表与可下载列表**分栏共存**（插件页只有一份列表）。
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        intro = QLabel(_INTRO)
        intro.setObjectName("sectionSubtitle")
        intro.setWordWrap(True)
        root.addWidget(intro)

        # ── 顶部状态栏（与插件页同：状态点 + 文本 + 右侧动作）──────────
        top_bar = QHBoxLayout()
        self._status = QLabel(_("正在读取组件注册表…"))
        self._status.setWordWrap(True)
        self._status.setAccessibleName(_("组件状态"))
        top_bar.addWidget(self._status, 1)
        if self._catalog_url:
            self._market_refresh = QPushButton(_("刷新"))
            self._market_refresh.clicked.connect(self._reload_catalog)
            top_bar.addWidget(self._market_refresh)
        else:
            self._market_refresh = None  # type: ignore[assignment]
        root.addLayout(top_bar)

        # ── 主体：左列表 + 右详情（与插件页 QSplitter 同形态）──────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左栏：**一份**组件列表（与插件页同形态）──────────────────────
        #
        # ★ 2026-11-24（用户第二轮反馈）：**不要再单列「已安装组件」**。
        # 插件页的做法是「同一份列表，装过的行首标 ✓」，只有一份可操作列表；
        # 而这里原先并排两份，用户得先想「我要找的东西在哪一份里」。
        # 现在统一成一份：市场条目与本地已装条目合并渲染，已装的带 ✓ 前缀，
        # 每个条目的动作（安装/卸载）由**当前选中项**决定，按钮按状态启用。
        left = QFrame()
        left.setProperty("card", True)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)

        list_header = QLabel(_("可用组件"))
        list_header.setObjectName("sectionSubtitle")
        left_layout.addWidget(list_header)

        # ★ 2026-11-24：**只在有 catalog 时**画搜索框。
        # 搜索的是市场条目；无 catalog 时列表只承载本地已装/离线导入的条目，
        # 给一个搜不到东西的框是误导。
        if self._catalog_url:
            self._market_search = QLineEdit()
            self._market_search.setPlaceholderText(_("搜索名称、ID、发布者或上游域名"))
            self._market_search.setClearButtonEnabled(True)
            self._market_search.textChanged.connect(self._reload_catalog_filtered)
            left_layout.addWidget(self._market_search)
        else:
            self._market_search = None  # type: ignore[assignment]

        self._market_list = QListWidget()
        self._market_list.setAccessibleName(_("组件列表"))
        self._market_list.setAlternatingRowColors(True)
        self._market_list.currentItemChanged.connect(self._on_component_selected)
        left_layout.addWidget(self._market_list, 1)

        splitter.addWidget(left)

        # ── 右栏：详情 / 浏览器内核（**两个标签页**，不再横贯底部）──────
        #
        # ★ 2026-11-24（用户第二轮反馈）：内核面板原先横贯整页底部，
        # 占掉一大条；而它其实是「组件的一种选择」——和详情同一层级。
        # 现在收进右栏的第二个标签页：默认看详情，要挑内核才切过去。
        right = QFrame()
        right.setProperty("card", True)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 14, 14, 14)
        right_layout.setSpacing(8)

        self._detail_tabs = QTabWidget()

        # ── 标签 1：组件详情与操作 ──
        detail_page = QWidget()
        detail_page_layout = QVBoxLayout(detail_page)
        detail_page_layout.setContentsMargins(0, 8, 0, 0)
        detail_page_layout.setSpacing(8)

        self._detail_title = QLabel(_("未选择组件"))
        self._detail_title.setObjectName("detailTitle")
        self._detail_title.setWordWrap(True)
        detail_page_layout.addWidget(self._detail_title)

        # 「下载前须知」与「已装组件属性」共用这一个可选中文本区，
        # 与插件页 `_detail_meta` 的位置对应。
        self._market_detail = QLabel(_("选择一个组件查看详情。"))
        self._market_detail.setObjectName("mutedLabel")
        self._market_detail.setWordWrap(True)
        self._market_detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._market_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_page_layout.addWidget(self._market_detail)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setAccessibleName(_("组件用途、兼容与卸载影响"))
        detail_page_layout.addWidget(self._details, 1)

        # 主操作：安装（市场）/ 卸载（已装）—— 按**当前选中项**启用
        primary_row = QHBoxLayout()
        if self._catalog_url:
            self._market_install = QPushButton(_("下载并安装所选组件"))
            self._market_install.setProperty("primary", True)
            self._market_install.clicked.connect(self._install_from_market)
        else:
            self._market_install = None  # type: ignore[assignment]
        self._uninstall_btn = QPushButton(_("卸载所选组件"))
        self._uninstall_btn.clicked.connect(lambda: self._run("uninstall"))
        self._rollback_btn = QPushButton(_("恢复本次卸载"))
        self._rollback_btn.clicked.connect(lambda: self._run("rollback"))
        self._verify_btn = QPushButton(_("校验所选组件"))
        self._verify_btn.clicked.connect(lambda: self._run("verify"))
        if self._market_install is not None:
            primary_row.addWidget(self._market_install)
        primary_row.addWidget(self._uninstall_btn)
        primary_row.addWidget(self._rollback_btn)
        primary_row.addWidget(self._verify_btn)
        primary_row.addStretch(1)
        detail_page_layout.addLayout(primary_row)

        # 离线包通道（组件独有）
        offline_row = QHBoxLayout()
        self._choose = QPushButton(_("选择离线组件包…"))
        self._choose.clicked.connect(self._pick_package)
        offline_row.addWidget(self._choose)
        self._package = QLabel(_("未选择组件包"))
        self._package.setWordWrap(True)
        offline_row.addWidget(self._package, 1)
        detail_page_layout.addLayout(offline_row)

        actions = QHBoxLayout()
        self._inspect_btn = QPushButton(_("检查所选包"))
        self._inspect_btn.clicked.connect(lambda: self._run("inspect"))
        self._import_btn = QPushButton(_("安装已检查的组件"))
        self._import_btn.clicked.connect(lambda: self._run("import"))
        self._reload_btn = QPushButton(_("刷新列表"))
        self._reload_btn.clicked.connect(self.refresh)
        for button in (self._inspect_btn, self._import_btn, self._reload_btn):
            actions.addWidget(button)
        actions.addStretch(1)
        detail_page_layout.addLayout(actions)

        self._detail_tabs.addTab(detail_page, _("组件详情"))

        # ── 标签 2：浏览器内核 ──
        self._kernel_panel = BrowserKernelPanel(self)
        self._kernel_panel.kernel_selected.connect(self.kernel_selected.emit)
        self._detail_tabs.addTab(self._kernel_panel, _("浏览器内核"))

        right_layout.addWidget(self._detail_tabs, 1)

        splitter.addWidget(right)
        # 左列表 / 右详情：插件页是 2:3，这里同样——重详情、轻列表。
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        splitter.setMinimumHeight(320)
        root.addWidget(splitter, 1)

        # ── 底部状态（市场拉取结果 / 失败源 / 索引新鲜度）──────────────
        self._market_status = QLabel("")
        self._market_status.setObjectName("mutedLabel")
        self._market_status.setWordWrap(True)
        root.addWidget(self._market_status)
        self._set_write_enabled(False)

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
        """重画**唯一那份**组件列表：市场条目 + 本地已装条目合并，已装的打 ✓。

        为什么合并（2026-11-24 用户第二轮反馈）：原先并排「可下载」与「已安装」
        两份列表，用户得先判断要找的东西在哪一份里。插件页只有一份，装过的
        行首标 ✓ —— 这里对齐。

        合并规则：

        * 市场条目全部列出（有 catalog 时）；
        * 本地已装但**不在市场目录里**的（离线导入的），补一条，同样打 ✓；
        * 同名以市场条目为准（它带完整声明信息）。
        """
        if self._market_list is None:
            return
        from .component_market_logic import filter_components

        query = self._market_search.text() if self._market_search is not None else ""
        visible = filter_components(self._entries, query)

        self._market_list.blockSignals(True)
        self._market_list.clear()
        listed_names: set[str] = set()
        for entry in visible:
            installed = entry.id in self._installed or entry.name in self._installed
            mark = "✓ " if installed else "— "
            state = _("[已安装]") if installed else _("[未安装]")
            item = QListWidgetItem(f"{mark}{state} {entry.name} {entry.version}")
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            item.setToolTip(_("上游：{0}").format(", ".join(entry.hosts) or _("无上游")))
            self._market_list.addItem(item)
            listed_names.add(entry.id)
            listed_names.add(entry.name)

        # 本地已装、但不在市场目录里的（离线 .ocp 导入的）——必须仍可卸载。
        for name, info in sorted(self._installed.items()):
            if name in listed_names:
                continue
            label = f"✓ {_('[已安装]')} {name} {info.get('version', '')}".strip()
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(_("本地导入，不在市场目录中"))
            self._market_list.addItem(item)
        self._market_list.blockSignals(False)

        if self._market_status is not None:
            total = len(self._entries)
            if query.strip():
                self._market_status.setText(
                    _("匹配 {n} / {total} 个组件。").format(n=len(visible), total=total)
                )
            elif total:
                self._market_status.setText(
                    _("共 {n} 个组件。选中可查看上游域名与下载量。").format(n=total)
                )

    def _selected_installed_info(self) -> dict[str, Any] | None:
        """当前选中项对应的「已安装」记录（没有则 None）。"""
        if self._market_list is None:
            return None
        item = self._market_list.currentItem()
        if item is None:
            return None
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        info = self._installed.get(key)
        if info is not None:
            return info
        entry = next((e for e in self._entries if e.id == key), None)
        return self._installed.get(entry.name) if entry is not None else None


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

    def _on_component_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None
    ) -> None:
        """选中任一条目（市场条目或本地已装条目）时刷新右栏。

        合并列表后不再区分「市场选中」与「已装选中」——条目 id 先按市场条目查，
        查不到再当成本地已装的名字。动作按钮由**条目状态**决定启用：
        已装的可卸载、未装的可安装。
        """
        if current is None:
            self._detail_title.setText(_("未选择组件"))
            self._market_detail.setText(_("选择一个组件查看详情。"))
            self._details.clear()
            self._set_write_enabled()
            return
        key = str(current.data(Qt.ItemDataRole.UserRole) or "")
        entry = next((e for e in self._entries if e.id == key), None)
        installed_info = self._installed.get(key)
        if installed_info is None and entry is not None:
            installed_info = self._installed.get(entry.name)

        if entry is not None:
            from .component_market_logic import human_bytes, pre_install_checks

            blockers, warnings = pre_install_checks(entry)
            parts = [
                f"{human_bytes(entry.declared_total_bytes)} · "
                f"{_('上游：')}{(', '.join(entry.hosts) or _('无上游'))}"
            ]
            if warnings:
                parts.append("；".join(warnings))
            if blockers:
                parts.append(_("无法安装：") + "；".join(blockers))
            self._market_detail.setText(" — ".join(parts))
            self._detail_title.setText(f"{entry.name} {entry.version}")
            self._details.setPlainText(
                _("安装通道：市场 recipe 装配\n声明体积：{0}\n上游域名：{1}").format(
                    human_bytes(entry.declared_total_bytes),
                    ", ".join(entry.hosts) or _("无上游"),
                )
            )
            blocked = bool(blockers)
        elif installed_info is not None:
            self._market_detail.setText(_("本地导入的组件，不在市场目录中。"))
            self._detail_title.setText(
                f"{key} {installed_info.get('version', '')}".strip()
            )
            self._details.setPlainText(_describe(installed_info))
            blocked = False
        else:
            self._detail_title.setText(key or _("未选择组件"))
            self._market_detail.setText("")
            self._details.clear()
            blocked = True

        # 按钮启用：已装 ⇒ 可卸载；未装且无阻断 ⇒ 可安装
        if self._market_install is not None:
            self._market_install.setEnabled(installed_info is None and not blocked)
        self._uninstall_btn.setEnabled(installed_info is not None)
        self._set_write_enabled()

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
        # 卸载的前置条件：注册表已加载 **且** 当前选中项是「已装」的那一个。
        self._uninstall_btn.setEnabled(bool(self._registry_sha) and bool(self._selected_installed_info()))
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
            item = self._market_list.currentItem() if self._market_list is not None else None
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
        """记录已装组件（供列表打 ✓），并重画**唯一那份**列表。"""
        self._registry_sha = str(result.get("registry_sha256", ""))
        self._installed = {
            str(info.get("name", "")): info for info in (result.get("components") or [])
        }
        self._reload_catalog_filtered()
        if self._installed:
            self._status.setText(_("已安装 {0} 个组件。").format(len(self._installed)))
        else:
            self._status.setText(_("尚未安装任何可选组件。"))
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

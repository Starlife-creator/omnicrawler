"""浏览器内核面板（§15.16.7 主界面）。

## 它解决什么

`browser` 能力槽位早就存在，但用户看不到也换不了 —— 内核隐藏在配置里。
这个面板把「有哪些内核、哪些可用、多大、来自哪、可信不可信」摆到界面上。

**数据源是 `services.browser_kernels.list_kernels()`**，不是界面自己推导。
CLI 的 `browser-kernels` 命令用同一个函数，两者不会漂移。

## 三处设计（对应 §15.16.7）

1. **列表 + 能力矩阵**：不只列名字，列**差异**（来源/大小/信任/适用）。
   用户真正要知道的是「换它能得到什么」。
2. **切换前的登录态提示**：换内核会让已有登录态失效（`engine_family` 绑定），
   这不是缺陷而是正确行为，但用户不知道 —— 必须明确提示。
3. **本地导入向导**：选文件 → 探测版本 → 提示匹配驱动 → 确认未验证。

## 为什么本地内核必须出现在同一张表里

隐藏状态比不支持还糟：用户自己导入了内核，却在界面里找不到，
只会以为「导入失败了」。所以它与内置/市场内核并列，只是带 `⚠ 未验证`。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...i18n import _
from ...services.browser_kernel_probe import probe_kernel
from ...services.browser_kernels import KernelOption, list_kernels

_INTRO = _(
    "浏览器内核决定动态页面怎么渲染。不同内核在站点兼容性与登录态上互不相通 ——"
    "切换内核会让已登录站点需要重新登录。"
)

#: 来源 → 面向用户的标签。
#:
#: **刻意不用颜色区分来源**：GUI 门禁对新文件零容忍裸十六进制色值，
#: 而硬编码颜色在深色/高对比度主题下必然不可读。来源的语义
#: （内置/市场/本地）用**文字**表达已经足够，且对色盲用户同样清晰。
#: 需要视觉强调时，应由设计体系令牌驱动，而不是在这个视图里写死颜色。
#: 来源标签。**必须用 `_()` 包裹**：这是面向用户的文案，
#: 不包会在 i18n 门禁里被判「未包裹的中文字面量」，
#: 且切换到其它语言时这几处会原样显示中文。
_TRUST_LABELS: dict[str, str] = {
    "builtin": _("内置"),
    "market": _("市场"),
    "local": _("本地导入"),
}


class BrowserKernelPanel(QWidget):
    """浏览器内核列表与切换面板。"""

    #: 用户切换了内核族（chromium / firefox / webkit / 空串=auto）。
    #:
    #: 为什么用信号而不是面板自己写配置：保存涉及 config_path、dirty 标记、
    #: 未保存提示，那些都由主窗口的 `_config_delegate` 管。面板广播意图，
    #: 主窗口决定怎么落盘 —— 职责分离，也避免两处各写一套保存逻辑。
    kernel_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, catalog: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("浏览器内核"))
        self._catalog = catalog
        self._options: list[KernelOption] = []
        self._local_kernels: list[dict[str, str]] = []
        self._current_provider = ""
        # **启动时读回上次选的内核族**。只写不读等于没持久化 ——
        # 那正是本项此前「仍未做」的实质（面板只改内存变量）。
        self._restore_preference()
        self._build_ui()
        self.refresh()

    def _restore_preference(self) -> None:
        """从 QSettings 读回上次选择的内核族（空串＝auto）。"""
        try:
            from ..settings import AppSettings

            stored = str(
                AppSettings.instance().value("browser/kernel_family", "", str) or ""
            ).strip()
            if stored:
                self._current_provider = stored
        except Exception:  # noqa: BLE001 - 读不到就按 auto
            pass

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        intro = QLabel(_INTRO)
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._table = QTableWidget(0, 5, self)
        self._table.setHorizontalHeaderLabels(
            [_("内核"), _("来源"), _("大小"), _("状态"), _("说明")]
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._table, 1)

        # 本地导入的探测结果（版本 / 需要哪个驱动）
        self._probe_label = QLabel("")
        self._probe_label.setWordWrap(True)
        self._probe_label.setVisible(False)
        root.addWidget(self._probe_label)

        buttons = QHBoxLayout()
        self._activate = QPushButton(_("设为默认"), self)
        self._activate.setEnabled(False)
        self._activate.clicked.connect(self._on_activate)
        buttons.addWidget(self._activate)

        self._import = QPushButton(_("导入本地内核…"), self)
        self._import.clicked.connect(self._on_import)
        buttons.addWidget(self._import)

        # 「从市场安装」：CLI 的 `components install-recipe` 早已可用
        # （§15.16.15 实测下载过 webkit 内核），但此前 GUI 只列不装 ——
        # 用户看到「内核文件缺失」却不知道怎么办。这里给出直接入口。
        self._install = QPushButton(_("从市场安装…"), self)
        self._install.clicked.connect(self._on_install)
        self._refresh = QPushButton(_("刷新"), self)
        self._refresh.clicked.connect(self.refresh)
        buttons.addWidget(self._install)
        buttons.addWidget(self._refresh)

        buttons.addStretch(1)
        self._status = QLabel("")
        buttons.addWidget(self._status)
        root.addLayout(buttons)

    # ── 数据 ──────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        """重新读取内核清单并重绘表格。"""
        self._options = list_kernels(catalog=self._catalog, local_kernels=self._local_kernels)
        self._table.setRowCount(len(self._options))
        for row, option in enumerate(self._options):
            self._table.setItem(row, 0, QTableWidgetItem(option.display_name))

            label = _TRUST_LABELS.get(option.source, option.source)
            source_item = QTableWidgetItem(label)
            # 不设置前景色：颜色应由设计体系（主题/样式表）统一决定。
            source_item.setToolTip(option.trust)
            self._table.setItem(row, 1, source_item)

            size_text = (
                f"{option.size_bytes // 1024 // 1024} MiB"
                if option.size_bytes
                else "—"
            )
            self._table.setItem(row, 2, QTableWidgetItem(size_text))

            state = _("可用") if option.available else _("缺失")
            state_item = QTableWidgetItem(state)
            if not option.available:
                state_item.setToolTip("\n".join(option.warnings) or _("内核文件缺失"))
            self._table.setItem(row, 3, state_item)

            note = option.description
            if option.warnings:
                note = f"{note}\n⚠ {'；'.join(option.warnings)}" if note else f"⚠ {'；'.join(option.warnings)}"
            self._table.setItem(row, 4, QTableWidgetItem(note))

        available = sum(1 for o in self._options if o.available)
        self._status.setText(
            _("共 {total} 个内核，{ok} 个可用").format(total=len(self._options), ok=available)
        )
        self._on_selection_changed()

    # ── 交互 ──────────────────────────────────────────────────────────────
    def _selected(self) -> KernelOption | None:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        return self._options[index] if 0 <= index < len(self._options) else None

    def _on_selection_changed(self) -> None:
        option = self._selected()
        self._activate.setEnabled(bool(option and option.available))
        if option is None:
            return
        self._activate.setToolTip(
            _("把「{name}」设为默认内核").format(name=option.display_name)
            if option.available
            else _("该内核不可用，无法设为默认")
        )

    def _on_activate(self) -> None:
        """切换默认内核 —— **切换前必须提示登录态会失效**。"""
        option = self._selected()
        if option is None or not option.available:
            return

        # §15.16.9：换内核会让已有登录态失效（engine_family 绑定）。
        # 这不是缺陷而是正确行为，但用户不知道 —— 必须明确说清代价。
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle(_("切换浏览器内核"))
        confirm.setText(
            _("确定切换到「{name}」？").format(name=option.display_name)
        )
        confirm.setInformativeText(
            _(
                "切换内核会使所有用其他内核登录过的站点**登录态失效**，需要重新登录。\n"
                "不同内核的 cookie 与指纹域互不相通，这是预期行为。"
            )
        )
        confirm.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return

        self._current_provider = option.provider_id
        # §15.16.14.7：**发出持久化信号**。早先只改内存里的
        # `_current_provider`，重启后回到 auto —— 用户以为「换过内核」，
        # 实际下次启动又变回去。
        #
        # 面板不直接写配置文件：保存由主窗口的 `_config_delegate` 负责
        # （它管着 path / dirty 标记 / 未保存提示）。这里只广播意图。
        family = str(option.engine_family or "").strip()
        # **持久化到 QSettings**：项目已有 `gui.settings.make_qsettings`，
        # 它在冻结便携模式下落到应用数据根内的 INI（脱离注册表、
        # 多份便携包互不串扰）。不自创新机制。
        try:
            # 用 `AppSettings` 而不是裸 QSettings：它**已处理本机这种场景**
            # （Windows 沙箱拒绝注册表写入），用内存回退保证本次会话仍有效。
            # 裸 QSettings 在本机 `isWritable()` 为 False、sync 返回 AccessError。
            from ..settings import AppSettings

            AppSettings.instance().set_value("browser/kernel_family", family)
        except Exception:  # noqa: BLE001 - 存不下不该打断切换
            pass
        self.kernel_selected.emit(family)
        self._status.setText(_("当前内核：{name}").format(name=option.display_name))

    def _on_install(self) -> None:
        """从市场安装当前选中项对应的组件（走 CLI 的 `install-recipe`）。

        **为什么不直接下载**：浏览器内核是 62–126 MiB 的归档，安装要算
        SHA-256、解压、生成声明文件 —— 那是 `install-recipe` 已经做对的事。
        在 GUI 里重写一遍只会分叉。这里只负责「告诉用户要装哪个」并调用它。
        """
        option = self._selected()
        if option is None:
            QMessageBox.information(
                self, _("从市场安装"), _("请先在列表里选中一个内核。")
            )
            return
        # `KernelOption` 的真实字段是 `component_id`（不是 recipe_file）。
        # 早先我用 getattr 猜了一个不存在的属性 —— 有默认值所以不崩，
        # 但功能永远走「没有关联配方」分支，等于没做。
        component_id = str(getattr(option, "component_id", "") or "").strip()
        if not component_id:
            QMessageBox.information(
                self,
                _("从市场安装"),
                _(
                    "「{name}」没有关联的市场组件，无法从这里安装。\n"
                    "可安装的内核需在市场里以组件形式发布。"
                ).format(name=option.display_name),
            )
            return
        # `install-recipe` 只接受 `--recipe <路径>`（没有「按 id 找」的选项）。
        # 分类目录只有市场知道，所以让用户去市场里定位 —— 这里给出组件 id
        # 与可复制的命令模板，而不是猜一个可能错的路径。
        recipe = component_id

        # 安装要联网下载几十到上百 MB，且不能阻塞 GUI 线程 ——
        # 这里只给出精确命令，让用户在自己的终端里跑并看到进度。
        # 起后台线程做下载会让「进度不可见 + 失败原因难查」。
        command = (
            "omnicrawler components install-recipe "
            '--recipe "' + str(_("<市场仓>/components/<分类>")) + "/" + recipe
            + '/recipe.json" --download'
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(_("从市场安装"))
        box.setText(_("安装「{name}」").format(name=option.display_name))
        box.setInformativeText(
            _(
                "本次安装需要联网下载构件，请在终端里执行：\n\n{cmd}\n\n"
                "装完点「刷新」即可在这里设为默认。"
            ).format(cmd=command)
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_import(self) -> None:
        """导入本地内核 —— 选文件 → 探测版本 → 提示驱动 → 确认未验证。"""
        path_text, _filter = QFileDialog.getOpenFileName(
            self,
            _("选择浏览器可执行文件"),
            "",
            _("可执行文件 (*.exe *.app *.bin *);;所有文件 (*)"),
        )
        if not path_text:
            return

        probe = probe_kernel(path_text)

        # 第 2 步：把探测结果摆给用户看（版本、探测方式、需要的驱动）
        lines = [
            _("文件：{path}").format(path=probe.path),
            _("版本：{version}（{how}）").format(
                version=probe.version or _("未探测到"),
                how=probe.detected_by or _("无"),
            ),
        ]
        hint = probe.driver_hint()
        if hint:
            lines.append(f"⚠ {hint}")
        for warning in probe.warnings:
            lines.append(f"⚠ {warning}")
        self._probe_label.setText("\n".join(lines))
        self._probe_label.setVisible(True)

        # 第 4 步：未验证来源必须显式确认 —— 能开，但绝不静默
        confirm = QMessageBox(self)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle(_("导入本地内核"))
        confirm.setText(_("确认导入这个内核？"))
        confirm.setInformativeText(
            "\n\n".join(lines)
            + "\n\n"
            + _("该内核**未经验证**（没有创作者签名），仅在本机使用。")
        )
        confirm.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return

        self._local_kernels.append(
            {
                "label": probe.path.name,
                "path": str(probe.path),
                "engine_family": probe.engine_family,
            }
        )
        self.refresh()
        self._status.setText(_("已导入本地内核：{name}").format(name=probe.path.name))

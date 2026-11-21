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
_TRUST_LABELS: dict[str, str] = {
    "builtin": "内置",
    "market": "市场",
    "local": "本地导入",
}


class BrowserKernelPanel(QWidget):
    """浏览器内核列表与切换面板。"""

    def __init__(self, parent: QWidget | None = None, *, catalog: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("浏览器内核"))
        self._catalog = catalog
        self._options: list[KernelOption] = []
        self._local_kernels: list[dict[str, str]] = []
        self._current_provider = ""
        self._build_ui()
        self.refresh()

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

        self._refresh = QPushButton(_("刷新"), self)
        self._refresh.clicked.connect(self.refresh)
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
        self._status.setText(_("当前内核：{name}").format(name=option.display_name))

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

"""从 MainWindow 抽离的三个项目级对话框（FINAL 长期债 #1 Phase A）。

- RunCompareFlow：运行对比（选两次运行 → 生成 diff 报告文件）
- PluginManagerDialog：插件启用/权限清单（仅收集选择；批准与持久化留在主窗口）
- ScheduleManagerDialog：本地定时任务 CRUD（配置落盘经回调交回主窗口）

抽离原则：对话框只管 UI 与自身域内逻辑；凡涉及 MainWindow 配置对象变更的
步骤，经回调/返回值交还主窗口，避免 _BaseDelegate 式的全量转发耦合。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...state import StateStore
from ..i18n import _
from ..widgets.toast import ToastManager

# ── 运行对比 ────────────────────────────────────────────────────────────


def show_run_comparison(parent: QWidget, *, project_root: Path, config_workspace: str) -> None:
    """选择两次运行并输出字段级对比报告到 <workspace>/output/。"""
    from ...review.run_compare import compare_runs

    workspace = Path(config_workspace).expanduser()
    if not workspace.is_absolute():
        workspace = project_root / workspace
    database = workspace / "state.sqlite3"
    if not database.is_file():
        ToastManager.instance().info(_("当前项目还没有可对比的运行记录。"))
        return
    with StateStore(database) as state:
        rows = state.rows(
            "SELECT run_id, started_at, status FROM runs ORDER BY started_at DESC LIMIT 30"
        )
        if len(rows) < 2:
            ToastManager.instance().info(_("至少完成两次运行后才能进行对比。"))
            return
        labels = [f"{row['started_at']} · {row['status']} · {row['run_id']}" for row in rows]
        before_label, ok = QInputDialog.getItem(parent, _("运行对比"), _("选择较早的一次运行："), labels, 1, False)
        if not ok:
            return
        after_label, ok = QInputDialog.getItem(parent, _("运行对比"), _("选择较新的一次运行："), labels, 0, False)
        if not ok:
            return
        before_id = rows[labels.index(before_label)]["run_id"]
        after_id = rows[labels.index(after_label)]["run_id"]
        if before_id == after_id:
            QMessageBox.warning(parent, _("运行对比"), _("请选择两次不同的运行。"))
            return
        report = compare_runs(state, str(before_id), str(after_id))
    output = workspace / "output" / f"run_comparison_{str(before_id)[:8]}_{str(after_id)[:8]}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    QMessageBox.information(
        parent, _("运行对比完成"),
        _(f"新增：{report['added']}\n修改：{report['modified']}\n") +

        _(f"确认删除：{report['removed']}\n可能删除：{report['possibly_removed']}\n\n报告：{output}"),
    )


# ── 插件管理 ────────────────────────────────────────────────────────────


class PluginManagerDialog(QDialog):
    """列出 plugins/ 目录下的插件与权限，让用户勾选要启用的集合。

    职责边界：本对话框只负责**收集选择**（selected_paths /
    requested_permissions）；权限批准确认与写入配置由调用方完成。
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        project_root: Path,
        current_paths: set[str],
        inspections: list,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("插件管理与权限"))
        self.resize(760, 460)
        self._project_root = project_root
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_("插件启用前只做静态检查；所需权限会明确列出，不会自动批准。")))
        self._listing = QListWidget(self)
        for inspection in inspections:
            state = _("兼容") if inspection.compatible else _("不可用")
            permissions = ", ".join(inspection.permissions) or _("无额外权限")
            item = QListWidgetItem(_(f"{inspection.name} {inspection.version} · {state} · 权限: {permissions}"))
            item.setData(Qt.ItemDataRole.UserRole, inspection)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = inspection.path in current_paths and inspection.compatible
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setToolTip(inspection.description + ("\n" + "\n".join(inspection.errors) if inspection.errors else ""))
            if not inspection.compatible:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._listing.addItem(item)
        layout.addWidget(self._listing)
        open_button = QPushButton(_("打开插件目录"))
        open_button.clicked.connect(self._open_plugin_dir)
        layout.addWidget(open_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_plugin_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._project_root / "plugins")))

    def collect_selection(self) -> tuple[list[str], set[str]]:
        """返回 (相对路径列表, 请求权限集合)；仅统计勾选且兼容的条目。"""
        selected: list[str] = []
        requested_permissions: set[str] = set()
        for row in range(self._listing.count()):
            row_item = self._listing.item(row)
            assert row_item is not None
            if row_item.checkState() != Qt.CheckState.Checked:
                continue
            inspection = row_item.data(Qt.ItemDataRole.UserRole)
            selected.append(str(Path(inspection.path).resolve().relative_to(self._project_root.resolve())))
            requested_permissions.update(inspection.permissions)
        return selected, requested_permissions

    def collect_permission_grants(self) -> dict[str, dict[str, object]]:
        """为勾选插件生成绑定版本、载荷哈希和作者指纹的权限授权。"""
        grants: dict[str, dict[str, object]] = {}
        for row in range(self._listing.count()):
            row_item = self._listing.item(row)
            assert row_item is not None
            if row_item.checkState() != Qt.CheckState.Checked:
                continue
            inspection = row_item.data(Qt.ItemDataRole.UserRole)
            if not inspection.permissions:
                continue
            grants[inspection.name] = {
                "version": inspection.version,
                "artifact_sha256": inspection.artifact_sha256,
                "creator_fingerprint": inspection.creator_fingerprint,
                "permissions": sorted(set(inspection.permissions)),
            }
        return grants


# ── 定时任务 ────────────────────────────────────────────────────────────


class ScheduleManagerDialog(QDialog):
    """Local schedule CRUD dialog.

    ``resolve_current_config`` is invoked when the user clicks "add current
    config": implementations must persist the live config and return its path,
    or return None when the user cancels saving (Chinese notes kept as
    comments to satisfy the i18n literal gate).
    """

    # 回调契约：确保当前配置已保存并返回路径；用户取消保存时返回 None。

    def __init__(
        self,
        parent: QWidget,
        *,
        database: Path,
        resolve_current_config: Callable[[], Path | None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("定时任务"))
        self.resize(680, 420)
        self._database = database
        self._resolve_current_config = resolve_current_config

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_(
            _("任务保存在本地；请用系统计划任务定期执行 omnicrawler schedule run-due。")
        )))
        self._schedule_list = QListWidget(self)
        layout.addWidget(self._schedule_list)

        form = QFormLayout()
        self._interval = QSpinBox(self)
        self._interval.setRange(1, 10080)
        self._interval.setValue(60)
        self._interval.setSuffix(_(" 分钟"))
        form.addRow(_("运行间隔"), self._interval)
        start_row = QHBoxLayout()
        self._start_date_label = QLineEdit()
        self._start_date_label.setReadOnly(True)
        self._start_date_label.setPlaceholderText(_("立即开始（点击选择日期）"))
        start_row.addWidget(self._start_date_label)
        start_date_btn = QPushButton("📅")
        start_date_btn.setFixedWidth(36)
        start_date_btn.setToolTip(_("选择首次运行日期"))
        start_date_btn.clicked.connect(self._pick_date)
        start_row.addWidget(start_date_btn)
        form.addRow(_("首次运行"), start_row)
        self._require_ac = QCheckBox(_("仅接通电源时运行"), self)
        self._require_ac.setChecked(True)
        form.addRow(_("电源条件"), self._require_ac)
        self._require_network = QCheckBox(_("需要可用网络接口"), self)
        self._require_network.setChecked(True)
        form.addRow(_("网络条件"), self._require_network)
        self._minimum_battery = QSpinBox(self)
        self._minimum_battery.setRange(0, 100)
        self._minimum_battery.setValue(30)
        self._minimum_battery.setSuffix("%")
        form.addRow(_("最低电量"), self._minimum_battery)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        add_button = QPushButton(_("添加当前配置"), self)
        toggle_button = QPushButton(_("启用/停用选中任务"), self)
        close_button = QPushButton(_("关闭"), self)
        buttons.addWidget(add_button)
        buttons.addWidget(toggle_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        add_button.clicked.connect(self._add_current)
        toggle_button.clicked.connect(self._toggle_current)
        close_button.clicked.connect(self.accept)
        self.refresh()

    def _pick_date(self) -> None:
        from ..widgets.calendar_popup import CalendarPopup

        popup = CalendarPopup(self)
        # A15：走公开信号 date_selected，不再访问私有 _calendar
        popup.date_selected.connect(self._start_date_label.setText)
        popup.exec()

    def refresh(self) -> None:
        from ...runtime.scheduler import ScheduleStore

        self._schedule_list.clear()
        with ScheduleStore(self._database) as store:
            for value in store.list():
                state = _("启用") if value["enabled"] else _("停用")
                minutes = max(1, int(value["interval_seconds"]) // 60)
                item = QListWidgetItem(
                    f"[{state}] {value['name']} — {minutes} {_('分钟')} — {value['config_path']}"
                )
                item.setData(Qt.ItemDataRole.UserRole, value["schedule_id"])
                item.setData(Qt.ItemDataRole.UserRole + 1, bool(value["enabled"]))
                self._schedule_list.addItem(item)

    def _add_current(self) -> None:
        from ...runtime.scheduler import ScheduleStore

        config_path = self._resolve_current_config()
        if config_path is None:
            return
        try:
            with ScheduleStore(self._database) as store:
                store.add(
                    config_path.stem,
                    config_path,
                    self._interval.value() * 60,
                    conditions={
                        "require_ac": self._require_ac.isChecked(),
                        "require_network": self._require_network.isChecked(),
                        "minimum_battery_percent": self._minimum_battery.value(),
                    },
                )
        except Exception as exc:
            QMessageBox.critical(self, _("添加失败"), str(exc))
            return
        self.refresh()

    def _toggle_current(self) -> None:
        from ...runtime.scheduler import ScheduleStore

        item = self._schedule_list.currentItem()
        if item is None:
            QMessageBox.information(self, _("提示"), _("请先选择一个任务。"))
            return
        try:
            with ScheduleStore(self._database) as store:
                store.set_enabled(
                    str(item.data(Qt.ItemDataRole.UserRole)),
                    not bool(item.data(Qt.ItemDataRole.UserRole + 1)),
                )
        except Exception as exc:
            QMessageBox.critical(self, _("更新失败"), str(exc))
            return
        self.refresh()

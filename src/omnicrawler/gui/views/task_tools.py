"""Saved-task tools using production services and owned background workers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...services.task_tools import TaskAction, execute, repair_binding
from ..core.background_worker import BackgroundWorker
from ..i18n import _


class TaskToolsWorker(BackgroundWorker):
    def __init__(self, action: TaskAction, parent: QWidget) -> None:
        super().__init__(parent)
        self.action = action

    def work(self) -> dict[str, Any]:
        if self.isInterruptionRequested():
            return {}
        return execute(self.action)


class TaskToolsDialog(QDialog):
    def __init__(self, config_path: Path, current_path: Callable[[], Path | None],
                 current_token: Callable[[], str], reload_config: Callable[[], None], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("模板、交付分析与恢复"))
        self.setAccessibleName(_("任务工具"))
        self.resize(760, 600)
        self.config_path = config_path.resolve()
        self._current_path = current_path
        self._current_token = current_token
        self._reload_config = reload_config
        self._token = current_token()
        self._config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
        self._worker: TaskToolsWorker | None = None
        self._close_pending = False
        self._delete_pending = False
        self._component_preview: dict[str, Any] = {}
        self._component_registry_sha = ""
        self._component_list_sha = ""
        self._component_recovery_sha = ""
        self._uninstalled_component = ""
        self._buttons: list[QPushButton] = []
        self._manifest_sha = ""
        self._preview_binding = ""
        self._report_path = ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_("基于当前已保存任务操作。分析默认本地；保存的模板仍需用新参数验证和试跑。")))
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName(_("任务工具分类"))
        layout.addWidget(self.tabs)
        workflow = self._tab(_("流程与诊断"))
        self.run_id = QLineEdit()
        self.run_id.setAccessibleName(_("运行 ID，留空查看当前任务最近运行"))
        workflow.addRow(_("运行 ID（可选）"), self.run_id)
        self._button(workflow, _("查看当前流程与实际运行"), lambda: self._launch("workflow", {"run_id": self.run_id.text().strip()}))
        self.workflow_steps = QListWidget()
        self.workflow_steps.setAccessibleName(_("任务流程步骤"))
        workflow.addRow(self.workflow_steps)
        self.workflow_details = QTextEdit()
        self.workflow_details.setReadOnly(True)
        self.workflow_details.setAccessibleName(_("所选步骤与验证建议"))
        workflow.addRow(self.workflow_details)
        self.workflow_steps.currentItemChanged.connect(self._show_step)
        self.replay_field = QLineEdit()
        self.replay_field.setAccessibleName(_("需要离线重放的字段名"))
        workflow.addRow(_("离线重放字段"), self.replay_field)
        self._button(workflow, _("用历史归档重放字段"), lambda: self._launch("replay", {
            "run_id": self.run_id.text().strip(), "field": self.replay_field.text().strip()}))
        workflow.addRow(QLabel(_("重放使用原运行归档，结果仅供比较，不修改正式数据。缺少归档时需重新试跑。")))
        capture = self._tab(_("保存为模板"))
        self.proof = self._file(capture, _("成功试跑摘要"))
        self.template_id = QLineEdit()
        self.template_id.setAccessibleName(_("模板标识"))
        capture.addRow(_("模板标识"), self.template_id)
        self.template_output = self._file(capture, _("模板保存位置"), save=True)
        self._button(capture, _("保存为模板"), lambda: self._launch("capture", {
            "proof": self.proof.text(), "output": self.template_output.text(), "template_id": self.template_id.text()}))
        analysis = self._tab(_("分析所选交付"))
        self.manifest = self._file(analysis, _("交付清单（JSON / JSONL）"))
        self._button(analysis, _("读取文档列表"), lambda: self._launch("sources", {"manifest": self.manifest.text()}))
        self.sources = QListWidget()
        self.sources.setAccessibleName(_("选择需要分析的文档"))
        analysis.addRow(self.sources)
        self.report_output = self._file(analysis, _("报告目录"), directory=True)
        self.use_ai = QCheckBox(_("使用已配置的 AI 分析所选文档摘录"))
        self.use_ai.setAccessibleName(_("允许发送所选文档摘录"))
        analysis.addRow(self.use_ai)
        self._button(analysis, _("分析所选文档"), self._analyze)
        recovery = self._tab(_("仅重试所选失败"))
        self._button(recovery, _("读取失败列表"), lambda: self._launch("failures", {}))
        self.failures = QListWidget()
        self.failures.setAccessibleName(_("选择需要重试的失败请求"))
        recovery.addRow(self.failures)
        recovery.addRow(QLabel(_("认证过期请求请先到登录会话页验证登录。重试只修改所选失败的队列状态，之后仍需恢复任务。")))
        self._button(recovery, _("仅重试所选失败"), self._retry)
        repair = self._tab(_("规则修复"))
        self.evidence = self._file(repair, _("独立快照证据"))
        self.candidate = self._file(repair, _("本地候选规则"))
        for operation, label in (("preview", _("预览比较")), ("apply", _("应用已预览候选")),
                                 ("observe", _("用新证据观察")), ("rollback", _("回滚本次修复"))):
            self._button(repair, label, lambda _checked=False, op=operation: self._repair(op))
        components = self._tab(_("可选组件"))
        components.addRow(QLabel(_("离线导入受信组件包；安装前检查兼容性与磁盘需求。运行时仍会校验组件内容。")))
        self.component_package = self._file(components, _("离线组件包"))
        self.component_package.textChanged.connect(lambda: self._component_preview.clear())
        self._button(components, _("检查离线包"), lambda: self._launch("components:inspect", {"package": self.component_package.text()}))
        self._button(components, _("安装已检查的组件"), lambda: self._manage_component("import"))
        self._button(components, _("读取已安装组件"), lambda: self._launch("components:list", {}))
        self.components = QListWidget()
        self.components.setAccessibleName(_("已安装的可选组件"))
        components.addRow(self.components)
        self.component_details = QTextEdit()
        self.component_details.setReadOnly(True)
        self.component_details.setAccessibleName(_("组件用途、兼容与卸载影响"))
        components.addRow(self.component_details)
        self.components.currentItemChanged.connect(self._show_component)
        self._button(components, _("卸载所选组件"), lambda: self._manage_component("uninstall"))
        self._button(components, _("恢复本次卸载"), lambda: self._manage_component("rollback"))
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setAccessibleName(_("操作结果和候选比较"))
        layout.addWidget(self.result_view)
        footer = QHBoxLayout()
        self.open_report = QPushButton(_("打开结果文件"))
        self.open_report.setEnabled(False)
        self.open_report.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self._report_path)))
        footer.addWidget(self.open_report)
        self.stop = QPushButton(_("结束操作并关闭"))
        self.stop.clicked.connect(self.reject)
        footer.addWidget(self.stop)
        layout.addLayout(footer)

    def _tab(self, label: str) -> QFormLayout:
        page = QWidget()
        form = QFormLayout(page)
        self.tabs.addTab(page, label)
        return form

    def _file(self, form: QFormLayout, label: str, *, save: bool = False, directory: bool = False) -> QLineEdit:
        field = QLineEdit()
        field.setAccessibleName(label)
        row = QHBoxLayout()
        row.addWidget(field)
        button = QPushButton(_("选择…"))
        button.setAccessibleName(label)
        button.setAutoDefault(False)
        def choose() -> None:
            if directory:
                path = QFileDialog.getExistingDirectory(self, label)
            elif save:
                path, _filter = QFileDialog.getSaveFileName(self, label)
            else:
                path, _filter = QFileDialog.getOpenFileName(self, label)
            if path:
                field.setText(path)
        button.clicked.connect(choose)
        row.addWidget(button)
        form.addRow(label, row)
        return field

    def _button(self, form: QFormLayout, label: str, callback: Callable[..., Any]) -> None:
        button = QPushButton(label)
        button.setAccessibleName(label)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        form.addRow(button)
        self._buttons.append(button)

    def _same_task(self) -> bool:
        path = self._current_path()
        return path is not None and path.resolve() == self.config_path and self._current_token() == self._token

    def _show_component(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if current is not None:
            self.component_details.setPlainText(self._component_text(current.data(Qt.ItemDataRole.UserRole)))

    @staticmethod
    def _component_text(info: dict[str, Any]) -> str:
        return _("组件：{0} {1}\n用途：{2}\n磁盘需求（字节）：{3}\n核心版本要求：{4}\n系统：{5}\n架构：{6}\n依赖：{7}\n卸载影响：{8}").format(
            info.get("name", ""), info.get("version", ""), info.get("purpose", ""), info.get("disk_bytes", 0),
            info.get("core_version") or _("未限定"), ", ".join(info.get("platforms", [])) or _("未限定"),
            ", ".join(info.get("architectures", [])) or _("未限定"), ", ".join(info.get("dependencies", [])) or _("无"),
            info.get("uninstall_impact", _("恢复时使用保留版本")))

    def _manage_component(self, operation: str) -> None:
        arguments: dict[str, Any] = {"confirmed": True, "registry_sha256": self._component_registry_sha}
        if operation == "import":
            preview = self._component_preview
            if not preview.get("compatible") or not preview.get("package_sha256"):
                self.result_view.setPlainText(_("请先检查当前离线包，解决签名、依赖与兼容问题。"))
                return
            arguments.update(package=self.component_package.text(), package_sha256=preview["package_sha256"],
                             registry_sha256=preview["registry_sha256"])
            detail = self._component_text(preview["component"])
        elif operation == "uninstall":
            selected = self.components.currentItem()
            if selected is None or not self._component_list_sha:
                self.result_view.setPlainText(_("请先读取组件信息并选择需要卸载的组件。"))
                return
            info = selected.data(Qt.ItemDataRole.UserRole)
            arguments["name"] = info["name"]
            arguments["registry_sha256"] = self._component_list_sha
            detail = self._component_text(info)
        else:
            if not self._uninstalled_component or not self._component_recovery_sha:
                self.result_view.setPlainText(_("本窗口没有可恢复的卸载；其它版本可通过组件命令恢复。"))
                return
            arguments["name"] = self._uninstalled_component
            arguments["registry_sha256"] = self._component_recovery_sha
            detail = _("恢复刚刚卸载的保留版本：{0}").format(self._uninstalled_component)
        if QMessageBox.question(self, _("确认组件操作"), detail,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self._launch("components:" + operation, arguments)

    def _component_done(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        if name.startswith("components:"):
            self._component_registry_sha = result.get("registry_sha256", "")
            if name == "components:list":
                self._component_list_sha = result.get("registry_sha256", "")
                self.components.clear()
                for info in result.get("components", []):
                    item = QListWidgetItem(f"{info['name']} {info['version']} — {info.get('purpose', '')}", self.components)
                    item.setData(Qt.ItemDataRole.UserRole, info)
                self.result_view.setPlainText(_("组件列表已读取；选择组件查看用途与卸载影响。"))
            elif name == "components:inspect":
                self._component_preview = result if arguments.get("package") == self.component_package.text() else {}
                self.component_details.setPlainText(self._component_text(result["component"]) + "\n" +
                    _("签名：已通过受信校验；兼容：{0}\n{1}").format(_("通过") if result.get("compatible") else _("未通过"), result.get("compatibility_reason", "")))
                self.result_view.setPlainText(_("离线包已检查，请核对详情后安装。"))
            else:
                self._component_preview.clear()
                self.components.clear()
                self._component_list_sha = ""
                self._component_recovery_sha = ""
                if name == "components:uninstall":
                    self._uninstalled_component = str(result["component"]["uninstalled"])
                    self._component_recovery_sha = result.get("registry_sha256", "")
                elif name == "components:rollback":
                    self._uninstalled_component = ""
                self.result_view.setPlainText(_("组件操作已完成；请重新读取列表核对。卸载保留可恢复的版本。"))
            return

    def _launch(self, name: str, arguments: dict[str, Any]) -> None:
        if self._worker is not None:
            return
        if not self._same_task():
            self.result_view.setPlainText(_("当前任务或草稿已变化，请保存后重新打开任务工具。"))
            return
        if name == "components:inspect":
            self._component_preview.clear()
        action = TaskAction(name, self.config_path, self._config_sha, arguments)
        worker = TaskToolsWorker(action, self)
        self._worker = worker
        self.tabs.setEnabled(False)
        self.open_report.setEnabled(False)
        self.result_view.setPlainText(_("正在处理，请稍候…"))
        worker.succeeded.connect(lambda result: self._done(name, arguments, result))
        worker.failed.connect(self.result_view.setPlainText)
        worker.interrupted.connect(lambda: self.result_view.setPlainText(_("操作已结束，请核对原任务的交付或配置。")))
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @staticmethod
    def _checked(widget: QListWidget) -> list[str]:
        return [str(widget.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(widget.count())
                if widget.item(index).checkState() == Qt.CheckState.Checked
                and widget.item(index).flags() & Qt.ItemFlag.ItemIsEnabled]

    def _analyze(self) -> None:
        ids = self._checked(self.sources)
        if not ids:
            self.result_view.setPlainText(_("请明确选择需要分析的文档。"))
            return
        if not self.manifest.text().strip() or not self.report_output.text().strip():
            self.result_view.setPlainText(_("请选择交付清单和报告目录。"))
            return
        self._launch("analyze", {"manifest": self.manifest.text(), "manifest_sha256": self._manifest_sha,
                    "output": self.report_output.text(), "selected_ids": ids, "use_ai": self.use_ai.isChecked()})

    def _confirm(self) -> bool:
        return QMessageBox.question(self, _("确认操作"), _("将修改当前已保存任务的所选队列或规则，继续吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _retry(self) -> None:
        fingerprints = self._checked(self.failures)
        if not fingerprints:
            self.result_view.setPlainText(_("请先选择需要重试的失败请求。"))
            return
        if self._confirm():
            self._launch("retry", {"fingerprints": fingerprints, "confirmed": True})

    def _binding(self) -> str:
        if not self.evidence.text() or not self.candidate.text():
            return ""
        try:
            return repair_binding(Path(self.evidence.text()), Path(self.candidate.text()))[0]
        except (OSError, ValueError):
            return ""

    def _repair(self, operation: str) -> None:
        if operation == "apply" and (not self._preview_binding or self._binding() != self._preview_binding):
            self.result_view.setPlainText(_("请先预览当前证据和候选；修改后需要重新预览。"))
            return
        if operation != "preview" and not self._confirm():
            return
        self._launch("repair:" + operation, {"evidence": self.evidence.text(), "candidate": self.candidate.text(),
                                              "confirmed": operation != "preview", "preview_binding": self._binding()})

    def _done(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        if self._close_pending:
            return
        if not self._same_task():
            self.result_view.setPlainText(_("操作结果属于原任务；当前任务已变化，请重新打开工具核对。"))
            return
        if name.startswith("components:"):
            self._component_done(name, arguments, result)
            return
        if name in {"sources", "failures"}:
            widget = self.sources if name == "sources" else self.failures
            widget.clear()
            key = "sources" if name == "sources" else "failures"
            for row in result[key]:
                item = QListWidgetItem(str(row.get("path") or row.get("url")), widget)
                item.setData(Qt.ItemDataRole.UserRole, row.get("id") or row.get("fingerprint"))
                item.setCheckState(Qt.CheckState.Unchecked)
                if row.get("reason") == "session_expired":
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                    item.setToolTip(_("请先验证登录后恢复"))
            self._manifest_sha = result.get("manifest_sha256", self._manifest_sha)
            self.result_view.setPlainText(_("列表已加载，请明确勾选需要处理的项目。"))
            return
        statuses = {
            "completed_local": _("本地事实报告已生成，请审阅。"),
            "completed_with_interpretations": _("分析报告已生成，模型解释仍需审阅。"),
            "paused": _("模型分析已暂停，本地事实保留；请检查隐私设置、预算和模型后重试。"),
            "preview": _("比较预览已生成；原配置未修改。"),
            "evidence_rejected": _("证据比较未通过，候选未应用。"),
            "approval_required": _("候选未达到应用条件，原配置未修改。"),
            "applied": _("候选已应用，请用独立的新证据观察。"),
            "observing": _("观察已记录，仍需新的独立证据。"),
            "stable": _("修复已达到稳定观察条件。"),
            "rolled_back": _("本次修复已回滚。"),
            "no_active_repair": _("没有需要回滚的活跃修复。"),
        }
        lines = [statuses.get(result.get("status", ""), _("操作已完成，请核对结果。"))]
        for row in result.get("candidates", []):
            candidate = row["candidate"]
            lines.append(_("字段：{0}\n原规则：{1}\n候选规则：{2}\n改善且兼容历史：{3}").format(
                candidate["field"], candidate["old_rule"], candidate["new_rule"], row["comparison"].get("improves_safely", False)))
        if name == "repair:preview":
            self._preview_binding = result.get("preview_binding", "") if result.get("preview_binding") == self._binding() and any(row["comparison"].get("improves_safely") for row in result.get("candidates", [])) else ""
        if name.startswith("repair:") and name != "repair:preview":
            self._reload_config()
            self._token = self._current_token()
            self._config_sha = hashlib.sha256(self.config_path.read_bytes()).hexdigest()
            lines.append(_("已刷新保存配置；被拒绝的候选不会应用。"))
        if name == "workflow":
            self.workflow_steps.clear()
            for step in result.get("stages", []):
                item = QListWidgetItem(step["title"] + " — " + self._runtime_label(step.get("runtime_status", "not_observed")))
                item.setData(Qt.ItemDataRole.UserRole, step)
                self.workflow_steps.addItem(item)
            if self.workflow_steps.count():
                self.workflow_steps.setCurrentRow(0)
            labels = {"missing": _("尚无试跑"), "matching_history": _("历史试跑匹配当前配置"),
                      "stale_or_incomplete": _("试跑过期或未完整成功"), "invalid": _("试跑记录无效")}
            lines.append(labels.get(result.get("trial", {}).get("state"), _("试跑状态未知")))
            runtime = result.get("runtime", {})
            if runtime.get("run_id"):
                self.run_id.setText(runtime["run_id"])
                lines.append(_("实际运行：{0}；状态：{1}；配置：{2}").format(
                    runtime["run_id"], runtime.get("status", "unknown"), runtime.get("config_match", "unknown")))
            lines.append(_("未记录步骤显示为未观察；历史配置匹配不代表当前任务已通过。"))
        if name == "replay":
            lines.append(_("历史内容重放；候选结果未写入正式数据。"))
            lines.append(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if "retried" in result:
            lines.append(_("已重入队：{0}。请回到任务页恢复运行。").format(result["retried"]))
        self._report_path = str(result.get("report") or result.get("created") or "")
        if self._report_path:
            lines.append(self._report_path)
            self.open_report.setEnabled(True)
        self.result_view.setPlainText("\n".join(lines))

    def _show_step(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            self.workflow_details.clear()
            return
        step = current.data(Qt.ItemDataRole.UserRole)
        self.workflow_details.setPlainText(_("配置：{0}\n\n实际记录：{1}\n{2}\n\n验证建议：{3}").format(
            step["detail"], self._runtime_label(step.get("runtime_status", "not_observed")),
            json.dumps(step.get("runtime_detail", {}), ensure_ascii=False, indent=2), step["check"]))

    @staticmethod
    def _runtime_label(status: str) -> str:
        return {"not_observed": _("未观察"), "completed": _("已记录完成"), "succeeded": _("已记录成功"),
                "failed": _("存在失败"), "running": _("运行中")}.get(status, status)

    def _finished(self) -> None:
        self._worker = None
        self.tabs.setEnabled(True)
        if self._close_pending:
            super().reject()
        if self._delete_pending:
            QCoreApplication.postEvent(self, QEvent(QEvent.Type.DeferredDelete))

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.DeferredDelete and self._worker is not None and self._worker.isRunning():
            self._delete_pending = True
            self.reject()
            return True
        return super().event(event)

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._close_pending = True
            self._worker.requestInterruption()
            self.result_view.setPlainText(_("正在结束操作，请稍候；结果保留在原任务。"))
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._worker is not None and self._worker.isRunning():
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)


def _config_token(config: Any) -> str:
    import copy
    import json

    import yaml

    from ..core.config_serializer import to_yaml

    # Serializer headers contain wall-clock time; compare semantic content and
    # serialize a copy because pruning orphan overrides mutates the model.
    return json.dumps(yaml.safe_load(to_yaml(copy.deepcopy(config))), sort_keys=True, ensure_ascii=False)


def open_task_tools(window: Any) -> None:
    from ..core.config_serializer import load_yaml

    path = window._config_path
    if path is None:
        QMessageBox.information(window, _("任务工具"), _("请先保存当前任务配置。"))
        return
    if _config_token(window._config) != _config_token(load_yaml(path)):
        QMessageBox.information(window, _("任务工具"), _("请先保存当前编辑，再打开任务工具。"))
        return
    dialog = TaskToolsDialog(path, lambda: window._config_path, lambda: _config_token(window._config),
                             lambda: window._config_delegate._open_recent(str(path)), window)
    dialog.exec()

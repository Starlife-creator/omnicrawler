"""Configuration new/open/save/import/export and history management."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from ..._version import __version__ as APP_VERSION  # noqa: N812
from ...templates.recipe_engine import compose_recipe, diff_config
from ...templates.template_catalog import bundled_template_catalog
from ..core.config_serializer import from_yaml, to_yaml
from ..core.workers import SiteInspectionWorker
from ..i18n import _
from ..template_library_dialog import TemplateLibraryDialog
from ..widgets.toast import ToastManager
from ._base import _BaseDelegate


def _safe_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


class ConfigManager(_BaseDelegate):
    """Configuration new/open/save/import/export and history management."""

    def new_config(self) -> None:
        mw = self._mw
        if mw._config.fields or mw._config.seed_urls:
            reply = QMessageBox.question(
                mw, _("新建配置"),
                _("当前配置有未保存的更改，是否继续？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        from ..core.config_model import CrawlConfig
        mw._config = CrawlConfig()
        mw._config_path = None
        mw._config_label.setText(_("未保存"))
        mw._refresh_canvas()
        mw._install_plugin_ui(notify=True)

    def open_config(self) -> None:
        mw = self._mw
        filepath, _filter = QFileDialog.getOpenFileName(
            mw, _("打开 YAML 配置"),
            str(mw._project_root / "configs"),
            _("YAML 文件 (*.yaml *.yml);;所有文件 (*)"))
        if filepath:
            self._open_recent(filepath)

    def _open_recent(self, filepath: str) -> None:
        mw = self._mw
        from ..core.config_serializer import load_yaml
        try:
            mw._config = load_yaml(Path(filepath))
            mw._config_path = Path(filepath)
            mw._bind_application_controllers()
            mw._config_label.setText(str(mw._config_path.name))
            mw._settings.add_recent_file(filepath)
            self.refresh_recent_menu()
            mw._refresh_canvas()
            mw._install_plugin_ui(notify=True)
            ToastManager.instance().success(_("配置已加载"))
        except Exception as e:
            QMessageBox.critical(mw, _("加载失败"), str(e))

    def save_config(self) -> None:
        mw = self._mw
        from ..core.config_serializer import save_yaml
        if not mw._config_path:
            self.save_config_as()
            return
        try:
            mw._config_history.snapshot(mw._config_path, reason="before_save")
            save_yaml(mw._config, mw._config_path)
            mw._bind_application_controllers()
            mw._settings.add_recent_file(str(mw._config_path))
            ToastManager.instance().success(_("已保存"))
        except Exception as e:
            QMessageBox.critical(mw, _("保存失败"), str(e))

    def save_config_as(self) -> None:
        mw = self._mw
        from ..core.config_serializer import save_yaml
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"{mw._config.project_name}_{timestamp}.yaml"
        filepath, _filter = QFileDialog.getSaveFileName(
            mw, _("另存为 YAML 配置"),
            str(mw._project_root / "configs" / default_name),
            _("YAML 文件 (*.yaml *.yml)"))
        if not filepath:
            return
        destination = Path(filepath)
        candidate = copy.deepcopy(mw._config)
        if mw._config_path and destination.resolve() != mw._config_path.resolve():
            revision = _("同一任务修订（保留身份）")
            independent = _("独立任务副本（新身份与工作区）")
            choice, accepted = QInputDialog.getItem(
                mw, _("另存为任务"), _("选择修订或独立副本；独立副本不会继承监控基线。"),
                [revision, independent], 0, False,
            )
            if not accepted:
                return
            if choice == independent:
                candidate.task_id = str(uuid4())
                project = candidate.passthrough.get("project")
                if isinstance(project, dict):
                    project.pop("identity_origin", None)
                candidate.created_at = datetime.now().isoformat()
                candidate.workspace = str(destination.parent / "work" / f"{destination.stem}_{candidate.task_id[:8]}")
        try:
            mw._config_history.snapshot(destination, reason="before_save_as")
            save_yaml(candidate, destination)
            mw._config = candidate
            mw._config_path = destination
            mw._bind_application_controllers()
            mw._config_label.setText(mw._config_path.name)
            mw._settings.add_recent_file(filepath)
            self.refresh_recent_menu()
            ToastManager.instance().success(_("配置已保存"))
        except Exception as e:
            QMessageBox.critical(mw, _("保存失败"), str(e))

    def refresh_recent_menu(self) -> None:
        mw = self._mw
        mw._recent_menu.clear()
        for filepath in mw._settings.recent_files:
            action = QAction(Path(filepath).name, mw)
            action.setToolTip(filepath)
            action.triggered.connect(lambda checked, p=filepath: self._open_recent(p))
            mw._recent_menu.addAction(action)
        if mw._settings.recent_files:
            mw._recent_menu.addSeparator()
            clear_action = QAction(_("清除最近文件"), mw)
            clear_action.triggered.connect(self.clear_recent)
            mw._recent_menu.addAction(clear_action)

    def clear_recent(self) -> None:
        mw = self._mw
        # A21：走公开接口，不再访问 _settings 私有成员
        mw._settings.clear_recent()
        self.refresh_recent_menu()

    def export_config_package(self) -> None:
        mw = self._mw
        import zipfile
        filepath, _filter = QFileDialog.getSaveFileName(
            mw, _("导出配置包"), f"{mw._config.project_name}.zip", _("ZIP 文件 (*.zip)"))
        if not filepath:
            return
        try:
            from ...security.security_audit import scan_config_text
            from ..core.config_serializer import to_yaml
            yaml_str = to_yaml(mw._config)
            report = scan_config_text(yaml_str)
            if not report["ok"]:
                lines = "、".join(str(item["line"]) for item in report["findings"])
                raise ValueError(
                    _("导出配置包含明文凭据（第 {} 行），已拒绝导出；请改用 secret:// 引用或环境变量。")
                    .format(lines)
                )
            with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("config.yaml", yaml_str)
                manifest = {"exported_at": datetime.now().isoformat(), "gui_version": APP_VERSION,
                            "config_name": mw._config.project_name, "task_id": mw._config.task_id}
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            ToastManager.instance().success(_("配置包已导出"))
        except Exception as e:
            QMessageBox.critical(mw, _("导出失败"), str(e))

    def import_config_package(self) -> None:
        mw = self._mw
        filepath, _filter = QFileDialog.getOpenFileName(
            mw, _("导入配置包"),
            str(mw._project_root),
            _("ZIP 文件 (*.zip);;所有文件 (*)"))
        if not filepath:
            return
        self._import_from_path(Path(filepath), confirm=True)

    def _import_from_path(self, path: Path, *, confirm: bool = False) -> None:
        mw = self._mw
        import zipfile

        from ..core.config_serializer import from_yaml
        try:
            with zipfile.ZipFile(path, "r") as zf:
                if "config.yaml" not in zf.namelist():
                    raise ValueError(_("无效的配置包：缺少 config.yaml"))
                yaml_str = zf.read("config.yaml").decode("utf-8")
                config = from_yaml(yaml_str)
                if confirm:
                    reply = QMessageBox.question(
                        mw, _("导入配置"),
                        _("即将导入配置 '{0}'，当前配置将被覆盖，是否继续？").format(config.project_name),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                mw._config = config
                mw._config_path = None
                mw._config_label.setText(_("已导入: ") + config.project_name)
                mw._refresh_canvas()
                ToastManager.instance().success(_("配置包已导入: {0}").format(path.name))
        except Exception as e:
            QMessageBox.critical(mw, _("导入失败"), str(e))

    def show_config_history(self) -> None:
        mw = self._mw
        from ..core.config_serializer import load_yaml
        if not mw._config_path:
            QMessageBox.information(mw, _("配置历史"), _("请先保存当前配置。"))
            return
        versions = mw._config_history.list(mw._config_path.stem)
        if not versions:
            QMessageBox.information(mw, _("配置历史"), _("保存第二个版本后即可在这里恢复。"))
            return
        dialog = QDialog(mw)
        dialog.setWindowTitle(_("配置历史与恢复"))
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(_("恢复前会自动备份当前配置。选择一个历史版本：")))
        listing = QListWidget(dialog)
        for version in versions:
            item = QListWidgetItem(f"{version.get('created_at', '')}  {version.get('reason', '')}  {version.get('sha256', '')[:10]}")
            item.setData(Qt.ItemDataRole.UserRole, version["path"])
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        _ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert _ok_btn is not None
        _ok_btn.setText(_("恢复所选版本"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        _current = listing.currentItem()
        if _current is None:
            return
        # S3.1.26：恢复前先校验——坏配置不覆盖当前文件
        from ..core.validator import plugin_source_kinds, validate_full_config

        version_path = Path(str(_current.data(Qt.ItemDataRole.UserRole)))
        try:
            candidate = load_yaml(version_path)
            errors, _warnings = validate_full_config(
                candidate,
                extra_source_kinds=plugin_source_kinds(mw._project_root),
            )
        except Exception as exc:
            QMessageBox.critical(
                mw, _("恢复失败"),
                _(f"该历史版本解析失败，未覆盖当前文件：{exc}"),
            )
            return
        if errors:
            QMessageBox.critical(
                mw, _("恢复失败"),
                _("该历史版本配置无效，未覆盖当前文件：\n") + "\n".join(errors),
            )
            return
        # 先备份再恢复；`restore` 自己会拒绝「覆盖前没有对应备份」的情况
        # （判据在 services/config_history.py，可脱 GUI 测试）。
        mw._config_history.snapshot(mw._config_path, reason="before_restore")
        try:
            mw._config_history.restore(version_path, mw._config_path)
        except ValueError as exc:
            QMessageBox.critical(mw, _("恢复失败"), str(exc))
            return
        mw._config = load_yaml(mw._config_path)
        mw._install_plugin_ui(notify=True)
        mw._refresh_canvas()
        ToastManager.instance().success(_("历史配置已恢复；如需撤销，可再次打开配置历史"))

    def show_template_library(self) -> None:
        mw = self._mw
        templates = mw._template_loader.discover_templates(force=True)
        if not templates:
            ToastManager.instance().info(_("未找到任何模板"))
            return
        dialog = TemplateLibraryDialog(templates, mw)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_template:
            return
        template = dialog.selected_template
        try:
            from ...templates.template_application import apply_template
            from ...templates.template_catalog import TemplateCatalog
            from ..views.template_parameters import TemplateParametersDialog

            recipe = yaml.safe_load(template.filepath.read_text(encoding="utf-8")) or {}
            current = yaml.safe_load(to_yaml(mw._config)) or {}
            catalog = TemplateCatalog(template.filepath.parent)
            record = next((item for item in catalog.discover() if item.path == template.filepath.resolve()), None)
            if record is None:
                raise ValueError(_("所选模板记录不可用"))
            if record.metadata.placeholders:
                initial: dict[str, Any] = {}
                seeds = current.get("source", {}).get("seeds", [])
                if seeds:
                    for name in record.metadata.placeholders:
                        if name.casefold() == "site_url":
                            initial[name] = seeds[0]
                parameters = TemplateParametersDialog(record.metadata.placeholders, initial, mw)
                if parameters.exec() != QDialog.DialogCode.Accepted:
                    return
                recipe = catalog.render(record, parameters.values(), strict=True)
            application = apply_template(current, recipe, preserve_choices=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(mw, _("模板不可用"), str(exc))
            return
        preview = "\n".join(
            f"{item.get('business_section', item['path'])}: "
            f"{item.get('business_change', item['change_type'])} — {item['path']}"
            for item in application.changes[:18]
        )
        box = QMessageBox(mw)
        box.setWindowTitle(_("组合模板"))
        box.setAccessibleName(_("组合模板确认"))
        box.setText(_("将模板能力组合到当前任务，不覆盖名称、入口网址、主题、已有字段和输出选择。"))
        box.setInformativeText(template.why or template.description)
        box.setDetailedText(preview or _("没有需要修改的配置"))
        box.setStandardButtons(QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Apply:
            return
        mw._config = from_yaml(yaml.safe_dump(application.after, allow_unicode=True, sort_keys=False))
        mw._config_path = None
        mw._config_label.setText(_("组合模板: ") + template.display_name)
        mw._refresh_canvas()
        if mw._config.has_placeholders():
            QMessageBox.warning(
                mw,
                _("存在占位符"),
                _("模板包含尚未替换的占位符（{{...}}），直接运行可能采集不到数据，请先在编辑器中替换。"),
            )
        else:
            ToastManager.instance().success(_("模板能力已组合；建议先试跑 3 页"))

    def inspect_site(self, url: str) -> None:
        mw = self._mw
        mw._statusbar.showMessage(_("正在安全探测网址并识别模板…"))
        worker = SiteInspectionWorker(
            url,
            mw._config.task_intent,
            robots_fail_closed=bool(
                (mw._config.passthrough.get("http") or {}).get("robots_fail_closed", True)
            ),
            parent=mw,
        )
        worker.succeeded.connect(self.on_site_inspected)
        worker.failed.connect(self.on_site_inspection_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self._finish_inspection_job(worker))
        mw._inspection_jobs.append(worker)
        worker.start()

    def _finish_inspection_job(self, worker: SiteInspectionWorker) -> None:
        mw = self._mw
        mw._inspection_jobs = [job for job in mw._inspection_jobs if job is not worker]
        mw._finish_deferred_close_if_safe()

    def on_site_inspection_failed(self, message: str) -> None:
        mw = self._mw
        if mw._close_after_background_jobs:
            return
        ToastManager.instance().error(_("智能识别失败"))
        QMessageBox.warning(
            mw,
            _("智能识别失败"),
            _("无法安全完成探测。原配置没有改变。\n\n") + message,
        )

    def on_site_inspected(self, payload: tuple[Any, ...]) -> None:
        mw = self._mw
        if mw._close_after_background_jobs:
            return
        report, url = payload
        recommendations = list(report.get("recommendations", []))
        lines = [
            _("页面类型: ") + str(report.get("page_type", "unknown")),
            _("动态页面: ") + (_("是") if report.get("dynamic") else _("否")),
            _("CMS: ") + (", ".join(report.get("cms", [])) or _("未识别")),
            _("分页: ") + (", ".join(report.get("pagination", [])) or _("未识别")),
            "",
            _("推荐模板:"),
        ]
        catalog = bundled_template_catalog([mw._project_root / "templates"])
        for index, item in enumerate(recommendations[:5], 1):
            record = catalog.get(str(item.get("id", "")))
            name = record.metadata.name if record else str(item.get("id"))
            reasons = "、".join(str(value) for value in item.get("reasons", []))
            lines.append(_(f"  {index}. {name} — {reasons or '安全通用回退'}"))
        if not recommendations:
            QMessageBox.information(mw, _("智能识别结果"), "\n".join(lines))
            return
        best_id = str(recommendations[0].get("id", ""))
        reply = QMessageBox.question(
            mw,
            _("智能识别结果"),
            "\n".join(lines) + "\n\n" + _("是否套用第一项模板？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        record = catalog.get(best_id)
        if record is None:
            QMessageBox.warning(mw, _("模板不可用"), best_id)
            return
        origin = QUrl(url).adjusted(QUrl.UrlFormattingOption.RemovePath).toString().rstrip("/")  # type: ignore[arg-type]
        values: dict[str, str] = {}
        for key in record.metadata.placeholders:
            lowered = key.casefold()
            if lowered == "site_url":
                values[key] = origin
            elif "url" in lowered:
                values[key] = url
        rendered = catalog.render(record, values, strict=False)
        current = yaml.safe_load(to_yaml(mw._config)) or {}
        composed = compose_recipe(current, rendered)
        changes = diff_config(current, composed)
        preview = [f"{change.path}: {change.before!r} → {change.after!r}" for change in changes[:18]]
        if len(changes) > 18:
            preview.append(_(f"…另有 {len(changes) - 18} 项"))
        confirm = QMessageBox(mw)
        confirm.setWindowTitle(_("组合建议预览"))
        confirm.setAccessibleName(_("组合建议预览"))
        confirm.setText(_("模板只会补充所需能力；任务名称、入口网址、主题、已有字段和输出选择将保留。"))
        confirm.setInformativeText(
            (record.metadata.why or record.metadata.description)
            + (_(f"\n限制：{record.metadata.limitations}") if record.metadata.limitations else "")
        )
        confirm.setDetailedText("\n".join(preview) or _("没有需要修改的配置"))
        confirm.setStandardButtons(QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Apply:
            return
        mw._config = from_yaml(yaml.safe_dump(composed, allow_unicode=True, sort_keys=False))
        mw._config_path = None
        mw._config_label.setText(_("智能模板: ") + record.metadata.name)
        mw._refresh_canvas()
        ToastManager.instance().info(_("智能模板已加载；请检查红色占位符后运行"))

    def open_ai_service_center(self) -> None:
        from ..views.ai_service_center import AIServiceCenterDialog

        mw = self._mw
        ai_config = self._load_ai_config_from_env()
        dialog = AIServiceCenterDialog(ai_config, mw, workspace=mw._project_root)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_ai_config_to_env(ai_config)

    def _load_ai_config_from_env(self) -> dict[str, Any]:
        from ...core.ai_env import load_ai_config_sidecar, load_ai_env

        mw = self._mw
        env_vars = load_ai_env(mw._project_root)
        config: dict[str, Any] = {"mode": "disabled"}
        provider = env_vars.get("OMNICRAWL_AI_PROVIDER", "disabled")
        if provider != "disabled":
            config["mode"] = "enabled"
            config["default_provider"] = "default"
            config.setdefault("providers", {})["default"] = {
                "type": provider,
                "base_url": env_vars.get("OMNICRAWL_AI_BASE_URL", ""),
                "model": env_vars.get("OMNICRAWL_AI_MODEL", ""),
                "api_key": env_vars.get("OMNICRAWL_AI_API_KEY", ""),
                "timeout_seconds": _safe_int(env_vars.get("OMNICRAWL_AI_TIMEOUT"), 60),
            }
        sidecar = load_ai_config_sidecar(mw._project_root)
        if sidecar:
            sidecar_providers = sidecar.get("providers", {})
            if isinstance(sidecar_providers, dict) and "default" in sidecar_providers:
                sidecar_providers["default"].pop("api_key", None)
            for key, value in sidecar.items():
                if key != "api_key":
                    config[key] = value
        return config

    def _save_ai_config_to_env(self, config: dict[str, Any]) -> None:
        from ...core.ai_env import save_ai_config_sidecar, save_ai_env, sync_ai_env_to_os
        from ...core.credentials import seal_secret

        mw = self._mw
        updates: dict[str, str | None] = {}
        if config.get("mode") == "disabled":
            updates["OMNICRAWL_AI_PROVIDER"] = "disabled"
            updates["OMNICRAWL_AI_BASE_URL"] = None
            updates["OMNICRAWL_AI_MODEL"] = None
            updates["OMNICRAWL_AI_API_KEY"] = None
            updates["OMNICRAWL_AI_TIMEOUT"] = None
        else:
            provider = config.get("providers", {}).get("default", {})
            api_key = str(provider.get("api_key", "")).strip()
            try:
                if api_key:
                    api_key = seal_secret("ai.env.OMNICRAWL_AI_API_KEY", api_key)
            except Exception:  # noqa: BLE001
                QMessageBox.warning(
                    mw,
                    _("无法安全保存"),
                    _(
                        "API key 无法加密存储（secrets_store 不可用），已拒绝写入明文，请检查系统凭据库或设置 {var}。"
                    ).format(var="OMNICRAWL_MASTER_PASSWORD"),
                )
                return
            updates["OMNICRAWL_AI_PROVIDER"] = provider.get("type", "openai_compatible")
            updates["OMNICRAWL_AI_BASE_URL"] = provider.get("base_url", "")
            updates["OMNICRAWL_AI_MODEL"] = provider.get("model", "")
            updates["OMNICRAWL_AI_API_KEY"] = api_key
            updates["OMNICRAWL_AI_TIMEOUT"] = str(provider.get("timeout_seconds", 60))
        save_ai_env(updates, project_root=mw._project_root)
        sync_ai_env_to_os(updates)
        save_ai_config_sidecar(mw._project_root, config)

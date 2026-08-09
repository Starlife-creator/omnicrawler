"""Configuration new/open/save/import/export and history management."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
)

from ... import __version__ as APP_VERSION  # noqa: N812
from ..i18n import _
from ..widgets.toast import ToastManager
from ._base import _BaseDelegate


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
        mw._rebuild_wizard()

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
            mw._refresh_recent_menu()
            mw._rebuild_wizard()
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
        try:
            mw._config_path = Path(filepath)
            mw._config_history.snapshot(mw._config_path, reason="before_save_as")
            save_yaml(mw._config, mw._config_path)
            mw._bind_application_controllers()
            mw._config_label.setText(mw._config_path.name)
            mw._settings.add_recent_file(filepath)
            mw._refresh_recent_menu()
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
                mw._rebuild_wizard()
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
        from ..core.validator import validate_full_config

        version_path = Path(str(_current.data(Qt.ItemDataRole.UserRole)))
        try:
            candidate = load_yaml(version_path)
            errors, _warnings = validate_full_config(candidate)
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
        mw._config_history.snapshot(mw._config_path, reason="before_restore")
        mw._config_history.restore(version_path, mw._config_path)
        mw._config = load_yaml(mw._config_path)
        mw._rebuild_wizard()
        ToastManager.instance().success(_("历史配置已恢复；如需撤销，可再次打开配置历史"))

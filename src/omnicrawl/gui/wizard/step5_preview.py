"""Step 5: YAML preview and save page with detailed trial run reporting."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWizardPage,
)

from ..core.config_model import CrawlConfig
from ..core.config_serializer import save_yaml, to_yaml
from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, SPACING, ThemeManager
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip


class Step5PreviewPage(QWizardPage):
    """Step 5: Preview and save."""

    config_changed = pyqtSignal()
    save_requested = pyqtSignal()
    save_as_requested = pyqtSignal()
    sample_requested = pyqtSignal()
    run_requested = pyqtSignal()

    def __init__(self, config: CrawlConfig, parent: QWizardPage | None = None) -> None:
        super().__init__(parent)
        self._config = config

        self.setTitle(_("步骤 5/5：预览、试跑与保存"))
        self.setSubTitle(_("确认系统建议；先试跑 3 页，再保存或运行完整任务。"))
        self.setAccessibleName(_("Step 5: 预览确认"))
        self.setAccessibleDescription(_("Step 5 of the OmniCrawler configuration wizard"))

        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()

        save_btn = QPushButton(_("保存配置"))
        save_btn.clicked.connect(self.save_requested.emit)
        toolbar.addWidget(save_btn)

        save_as_btn = QPushButton(_("另存为…"))
        save_as_btn.clicked.connect(self.save_as_requested.emit)
        toolbar.addWidget(save_as_btn)

        refresh_btn = QPushButton(_("刷新预览"))
        refresh_btn.clicked.connect(self.refresh_preview)
        toolbar.addWidget(refresh_btn)

        sample_btn = QPushButton(_("试跑 3 页"))
        sample_btn.clicked.connect(self.sample_requested.emit)
        toolbar.addWidget(sample_btn)
        toolbar.addWidget(HelpTooltip("tryrun.plan"))

        run_btn = QPushButton(_("保存并运行"))
        run_btn.clicked.connect(self.run_requested.emit)
        toolbar.addWidget(run_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setObjectName("previewSummary")
        self._apply_summary_style()
        ThemeManager.instance().theme_changed.connect(self._apply_summary_style)
        layout.addWidget(self._summary)

        self._advanced_btn = QPushButton(_("显示高级 YAML 配置"))
        self._advanced_btn.setCheckable(True)
        self._advanced_btn.toggled.connect(self._toggle_advanced)
        layout.addWidget(self._advanced_btn)

        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setVisible(False)
        self._preview.setObjectName("yamlPreview")
        self._preview.setProperty("codeEditor", True)
        self._preview.setFont(QFont(FONT_FAMILY_MONO.split(", ")[0], 10))
        self._apply_preview_style()
        ThemeManager.instance().theme_changed.connect(self._apply_preview_style)
        layout.addWidget(self._preview)

    def initializePage(self) -> None:
        self.refresh_preview()

    def _apply_summary_style(self, *_args) -> None:
        """从设计令牌生成摘要面板样式，自动跟随主题。"""
        t = ThemeManager.instance().tokens
        self._summary.setStyleSheet(
            f"padding: {SPACING['lg']}px; background: {t.info_bg}; " +

            f"border: 1px solid {t.border}; border-radius: {RADIUS['md']}px;"
        )

    def _apply_preview_style(self, *_args) -> None:
        """从设计令牌生成 YAML 预览样式，自动跟随主题。"""
        t = ThemeManager.instance().tokens
        self._preview.setStyleSheet(f"""
            QPlainTextEdit#yamlPreview {{
                background-color: {t.code_bg};
                color: {t.code_fg};
                border: 1px solid {t.code_border};
                border-radius: {RADIUS["sm"]}px;
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
        """)

    def validatePage(self) -> bool:
        return True

    def show_sample_result(self, result: dict[str, Any]) -> None:
        """Show detailed trial run results."""
        sample = result.get("sample", {})
        status = sample.get("status", "unknown")
        pages = sample.get("processed", 0)
        records = sample.get("records", 0)
        fields_found = sample.get("fields_found", [])
        files_downloaded = sample.get("files_downloaded", 0)
        ocr_used = sample.get("ocr_used", False)
        excluded_items = sample.get("excluded", [])
        failed_pages = sample.get("failed_pages", [])
        field_samples = sample.get("field_samples", {})
        quality_issues = sample.get("quality_issues", [])
        scope_info = sample.get("scope", {})

        status_icon = {"ok": "OK", "partial": "PARTIAL", "error": "FAILED"}.get(status, "?")
        status_text = {"ok": "Success", "partial": "Partial Success", "error": "Failed"}.get(status, status)

        lines = [
            f"## Trial Run: {status_icon} {status_text}",
            "",
            "### Scope",
            f"- URLs visited: {', '.join(scope_info.get('visited_urls', ['none']))}",
            f"- Out of scope: {'YES - check settings' if scope_info.get('out_of_scope') else 'No'}",
            f"- Method: {scope_info.get('method', 'HTTP')}",
            "",
            "### Statistics",
            f"- Pages processed: {pages}",
            f"- Records extracted: {records}",
            f"- Fields found: {', '.join(fields_found) if fields_found else 'auto-extracted'}",
        ]

        if field_samples:
            lines.append("")
            lines.append("### Field Samples")
            for field_name, values in field_samples.items():
                sample_values = values if isinstance(values, list) else [values]
                lines.append(f"- **{field_name}**: {', '.join(str(v)[:50] for v in sample_values[:3])}")

        if files_downloaded:
            lines.append("")
            lines.append(f"### Files Downloaded: {files_downloaded}")
        if ocr_used:
            lines.append("- OCR was used")

        if excluded_items:
            lines.append("")
            lines.append("### Excluded Items")
            for item in excluded_items[:10]:
                lines.append(f"- {item.get('url', '')}: {item.get('reason', '')}")

        if failed_pages:
            lines.append("")
            lines.append("### Failed Pages")
            for page in failed_pages[:5]:
                lines.append(f"- {page.get('url', '')}: {page.get('error', '')}")

        if quality_issues:
            lines.append("")
            lines.append("### Quality Issues")
            for issue in quality_issues[:5]:
                lines.append(f"- {issue.get('field', '')}: {issue.get('issue', '')}")

        lines.append("")
        lines.append("### Recommended Actions")
        lines.append("- If scope correct: accept and run")
        lines.append("- If scope wrong: adjust scope -> re-trial")
        lines.append("- If fields wrong: adjust field design -> re-trial")
        lines.append("- If pages failed: lower concurrency -> retry failed only")
        lines.append("- If files missing: check extensions -> re-trial")

        self._summary.setText("\n".join(lines))

    def refresh_preview(self) -> None:
        try:
            yaml_str = to_yaml(self._config)
            self._preview.setPlainText(yaml_str)
            source_names = {
                "static_html": _("Static HTML"),
                "browser": _("Browser (Dynamic)"),
                "rest": _("REST API"),
                "feed": _("RSS/Feed"),
            }
            resource_profile_names = {
                "economy": _("Economy (2 concurrent)"),
                "balanced": _("Balanced (recommended)"),
                "performance": _("Performance (plugged in)"),
            }
            self._summary.setText(_(
                "## Config Summary\n\n" +

                "Project: {0}\nSource type: {1}\nSeed URLs: {2}\nFields: {3}\n" +

                "Max pages: {4}\nConcurrency: {5}\nResource mode: {10}\n" +

                "Downloads: {6}\nTopic filter: {7}\n" +

                "PDF processing: {8}\nChange monitoring: {9}\nAI: {11}\n\n" +

                "---\n" +

                "**Why these settings?**\n" +

                "- Source type: auto-detected from seed URL; trial run will verify\n" +

                "- Concurrency & limits: protect target site and local resources\n" +

                "- Fields: auto-extract title, body, source, link when not specified\n\n" +

                "**Next steps:**\n" +

                "1. Click 'Trial Run (3 pages)' to verify scope, fields and files\n" +

                "2. If correct, click 'Save & Run' to execute\n" +

                "3. If not, go back to adjust before re-trying"
            ).format(
                self._config.project_name,
                source_names.get(self._config.source_kind, self._config.source_kind),
                len(self._config.seed_urls),
                len(self._config.fields),
                self._config.max_pages,
                self._config.concurrency,
                _("On") if self._config.download.enabled else _("Off"),
                ", ".join(self._config.topic_include_any + self._config.topic_include_all) or _("None"),
                _("On") if self._config.process_pdf else _("Off"),
                _("On") if self._config.monitor_same_url else _("Off"),
                resource_profile_names.get(
                    getattr(self._config, 'resource_profile', 'balanced'),
                    _("Balanced"),
                ),
                self._config.ai_mode if getattr(self._config, 'ai_mode', 'disabled') != 'disabled'
                else _("Off (no AI needed)"),
            ))
        except Exception as e:
            self._preview.setPlainText(f"# YAML generation failed: {e}")
            self._summary.setText(_("Config preview failed: {0}").format(e))

    def _toggle_advanced(self, checked: bool) -> None:
        self._preview.setVisible(checked)
        self._advanced_btn.setText(
            _("隐藏高级 YAML") if checked else _("显示高级 YAML 配置")
        )

    def _save_config(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self._config.project_name}_{timestamp}.yaml"

        from ..settings import AppSettings
        settings = AppSettings.instance()
        project_root = Path(settings.project_root) if settings.project_root else Path.cwd()
        configs_dir = project_root / "configs"
        configs_dir.mkdir(parents=True, exist_ok=True)

        filepath = configs_dir / filename
        try:
            save_yaml(self._config, filepath)
            self._last_saved_path = filepath
            self.setSubTitle(_(f"Config saved to: {filepath}"))
            self.config_changed.emit()
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, _("Save Failed"), str(e))

    def _save_as(self) -> None:
        filepath, _selected_filter = QFileDialog.getSaveFileName(
            self, _("Save YAML Config As"),
            f"{self._config.project_name}.yaml",
            _("YAML Files (*.yaml *.yml)"),
        )
        if not filepath:
            return
        try:
            save_yaml(self._config, Path(filepath))
            self._last_saved_path = Path(filepath)
            self.setSubTitle(_(f"Config saved to: {filepath}"))
            self.config_changed.emit()
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, _("Save Failed"), str(e))

    @property
    def last_saved_path(self) -> Path | None:
        return getattr(self, "_last_saved_path", None)

"""Step 5: YAML preview and save page with detailed trial run reporting."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWizardPage,
)

from ..core.config_model import CrawlConfig
from ..core.config_serializer import save_yaml, to_yaml
from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, SPACING, ThemeManager
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip

# 覆盖下拉第一项：代表「不覆盖，按 Categorizer 自动推荐」
_AUTO_HINT_KEY = "__auto__"
_AUTO_HINT_LABEL = _("（自动推荐 / 不覆盖）")


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

        # B-2：模板推荐闸门（L1+L2 本地规则，默认不启用 L3 嗅探，避免 GUI 内联网）
        rec_box = QGroupBox(_("模板推荐闸门（B-2）"))
        rec_layout = QVBoxLayout(rec_box)
        rec_layout.setSpacing(8)
        rec_head = QHBoxLayout()
        self._rec_badge = QLabel(_("尚未分析"))
        self._rec_badge.setObjectName("categorizeBadge")
        self._rec_badge.setProperty("badge", "info")
        self._rec_summary_label = QLabel(
            _("点击右侧按钮，基于 L1 扩展名/L2 本地映射表，对 Seed URLs 做自动模板推荐并分流为「自动放行 / 待人工确认」。")
        )
        self._rec_summary_label.setWordWrap(True)
        rec_refresh_btn = QPushButton(_("重新分析模板推荐"))
        rec_refresh_btn.clicked.connect(self._refresh_recommendation_gate)
        rec_head.addWidget(self._rec_badge)
        rec_head.addWidget(self._rec_summary_label, 1)
        rec_head.addWidget(rec_refresh_btn)
        rec_layout.addLayout(rec_head)
        self._rec_detail = QTableWidget(0, 6)
        self._rec_detail.setHorizontalHeaderLabels([
            _("URL"),
            _("命中来源"),
            _("置信度"),
            _("推荐模板"),
            _("闸门决策"),
            _("覆盖模板（可手动指定）"),
        ])
        self._rec_detail.verticalHeader().setVisible(False)
        self._rec_detail.setAlternatingRowColors(True)
        self._rec_detail.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._rec_detail.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._rec_detail.setFixedHeight(240)
        hdr = self._rec_detail.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for ci in (1, 2, 3, 4):
            hdr.setSectionResizeMode(ci, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._rec_detail.setColumnWidth(2, 80)
        rec_layout.addWidget(self._rec_detail)
        layout.addWidget(rec_box)

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

        # B-2 闸门：每次 refresh_preview 一并分析（异步感不重要；seed_urls 数量通常 <200）
        try:
            self._refresh_recommendation_gate()
        except Exception as e:  # noqa: BLE001 — 闸门失败不影响任务预览主流程
            self._rec_badge.setText(_("模板推荐失败"))
            self._rec_badge.setProperty("badge", "warning")
            self._rec_summary_label.setText(str(e))

    def _refresh_recommendation_gate(self) -> None:
        """运行 B-2 分类 + ConfirmationEngine 并刷新闸门 UI（表格 + 每行覆盖下拉）。

        策略：GUI 侧默认**不传 fetcher** → 只用 L1（扩展名硬止损）+ L2（本地 YAML 映射表）。
        需要 L3 受限嗅探（HEAD + Range 0-8192）的场景可由 CLI wizard 或单独的 Doctor 工具触发，
        避免在 5 步 Wizard 里因网络请求卡顿或被审计拒绝而中断体验。
        """
        urls = list(getattr(self._config, "seed_urls", []) or [])
        if not urls:
            self._rec_badge.setText(_("无种子 URL"))
            self._rec_badge.setProperty("badge", "info")
            self._rec_summary_label.setText(_("请返回步骤 2 添加至少 1 条种子 URL。"))
            self._rec_detail.setRowCount(0)
            return

        # Lazy 导入：避免 wizard 模块 import 时过早加载 Categorizer（它会在 __init__ 里读 YAML）
        from omnicrawl.core import categorizer as _cat_mod
        from omnicrawl.core.categorizer import (
            RecommendationConfirmationEngine,
            SiteCategorizer,
        )
        try:
            sc = SiteCategorizer()
        except Exception as exc:
            self._rec_badge.setText(_("Categorizer 初始化失败"))
            self._rec_badge.setProperty("badge", "warning")
            self._rec_summary_label.setText(str(exc))
            self._rec_detail.setRowCount(0)
            return

        classify_summary = sc.classify(urls, catalog=None, fetcher=None)
        engine = RecommendationConfirmationEngine()
        gate = engine.process(classify_summary)
        self._gate_threshold = float(getattr(engine, "auto_threshold", engine.DEFAULT_THRESHOLD))

        # 收集下拉模板选项：从 categorizer 模块扫描所有 _T_* + _FINAL_FALLBACK 常量，去重值，稳定排序
        known_tpls: list[str] = []
        seen = set()
        for const_name in sorted(dir(_cat_mod)):
            if const_name.startswith("_T_") or const_name == "_FINAL_FALLBACK_TEMPLATE":
                v = getattr(_cat_mod, const_name)
                if isinstance(v, str) and v and v not in seen:
                    seen.add(v)
                    known_tpls.append(v)
        known_tpls.sort()

        # 若存在 per_url_template_overrides，则对覆盖 URL 的 CategorizeResult 做强制替换，
        # 并重新走 engine.decide 得到新的 ConfirmationDecision（覆盖=100% 置信，强制自动放行，类似 L1）
        overrides = getattr(self._config, "per_url_template_overrides", None) or {}
        ordered: list[tuple[Any, Any]] = []
        auto_after = 0
        human_after = 0
        from omnicrawl.core.categorizer import CategorizeResult, ConfirmationDecision  # lazy
        for r, d in list(gate.auto_rows) + list(gate.human_rows):
            forced = overrides.get(r.url)
            if forced and forced in known_tpls and forced != r.template_id:
                new_r = CategorizeResult(
                    url=r.url,
                    template_id=forced,
                    confidence=1.00,
                    hit_source="MANUAL",
                    fallback_used=False,
                    reason=_("用户在 Step5 闸门手动覆盖模板"),
                    raw_requested_template=forced,
                )
                new_d = ConfirmationDecision(
                    auto_approved=True,
                    approved_reason=_("手动覆盖视为强信号自动放行"),
                    human_hint="",
                    threshold_used=self._gate_threshold,
                )
                ordered.append((new_r, new_d))
                auto_after += 1
            else:
                ordered.append((r, d))
                if d.auto_approved:
                    auto_after += 1
                else:
                    human_after += 1
        total_after = auto_after + human_after
        self._populate_gate_table(ordered, known_tpls, self._gate_threshold)

        # Badge + summary（含人工覆盖计数叠加）
        overrides_count = sum(1 for v in overrides.values() if v)
        if total_after == 0:
            badge = _("无可用结果")
            level = "info"
        elif human_after == 0:
            badge = _("✓ 全部自动放行 {0}/{1}").format(auto_after, total_after)
            level = "success"
        elif auto_after == 0:
            badge = _("⚠ {0}/{1} 需要人工确认").format(human_after, total_after)
            level = "danger"
        else:
            badge = _("部分自动：{0}自动 / {1}待人工").format(auto_after, human_after)
            level = "warning"
        if overrides_count:
            badge = _("{0} · 手动覆盖 {1} 条").format(badge, overrides_count)
        self._rec_badge.setText(badge)
        self._rec_badge.setProperty("badge", level)
        self._rec_summary_label.setText(
            _("闸门阈值 confidence ≥ {0:.2f}；L1 永远自动；fallback 兜底强制人工。"
              "每行末列可下拉切换「覆盖模板」：空=留自动，选中=强制用该模板（写入 YAML source.seed_template_overrides）。"
              "当前手动覆盖：{1} 条。").format(self._gate_threshold, overrides_count)
        )

        # _repolish：刷新 QSS 动态属性（badge info/warning/success/danger 变色）
        style = self._rec_badge.style()
        if style:
            style.unpolish(self._rec_badge)
            style.polish(self._rec_badge)
        self._rec_badge.ensurePolished()

    def _populate_gate_table(
        self,
        ordered_rows: list[tuple[Any, Any]],
        template_options: list[str],
        threshold: float,
    ) -> None:
        """把 (CategorizeResult, ConfirmationDecision) 对填入 QTableWidget，末列下拉。"""
        overrides = getattr(self._config, "per_url_template_overrides", None) or {}
        tw: QTableWidget = self._rec_detail
        tw.setRowCount(0)
        tw.setRowCount(len(ordered_rows))

        _conf_label = lambda r, d: (
            _("✓ 自动放行 · {0}").format(d.approved_reason) if d.auto_approved
            else _("⚠ 待人工 · {0}").format(d.human_hint or _("置信度 < {0:.2f}").format(threshold))
        )

        for row_i, (r, d) in enumerate(ordered_rows):
            # 0: URL
            url_item = QTableWidgetItem(r.url)
            url_item.setToolTip(r.url)
            url_item.setData(Qt.ItemDataRole.UserRole, r.url)
            tw.setItem(row_i, 0, url_item)

            # 1: 命中来源
            src_map = {
                "L1": _("L1 扩展名/权威后缀"),
                "L2": _("L2 本地 eTLD1 映射"),
                "L3": _("L3 受限嗅探"),
                "FALLBACK": _("Fallback 大类兜底"),
            }
            src_display = src_map.get(r.hit_source, r.hit_source)
            src_item = QTableWidgetItem(f"{src_display} · {r.reason[:40]}")
            src_item.setToolTip(r.reason or "")
            tw.setItem(row_i, 1, src_item)

            # 2: 置信度
            pct = max(0.0, min(1.0, float(r.confidence or 0.0)))
            conf_item = QTableWidgetItem(f"{pct * 100:.0f}%")
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            conf_item.setToolTip(f"confidence = {pct:.3f}")
            tw.setItem(row_i, 2, conf_item)

            # 3: 推荐模板
            rec = r.template_id
            rec_item = QTableWidgetItem(rec)
            if r.fallback_used:
                rec_item.setToolTip(_("（大类兜底：原始 L2 映射结果 {0} 不存在于模板目录）").format(r.raw_requested_template))
            tw.setItem(row_i, 3, rec_item)

            # 4: 闸门决策
            decision = _conf_label(r, d)
            dec_item = QTableWidgetItem(decision)
            dec_item.setForeground(
                dec_item.foreground() if d.auto_approved else dec_item.background()
            )
            tw.setItem(row_i, 4, dec_item)

            # 5: 覆盖模板（下拉）
            cb = QComboBox()
            cb.addItem(_AUTO_HINT_LABEL, _AUTO_HINT_KEY)
            for tpl in template_options:
                # 在下拉里加后缀：若当前自动推荐就是这个，标注「推荐」
                suffix = ""
                if tpl == rec:
                    suffix = " " + _("※推荐")
                cb.addItem(tpl + suffix, tpl)
            # 若已有覆盖，预选对应项
            existing = overrides.get(r.url, "")
            if existing and existing in template_options:
                idx = cb.findData(existing)
                if idx >= 0:
                    cb.setCurrentIndex(idx)
            cb.setProperty("gate_url", r.url)
            cb.currentIndexChanged.connect(lambda _i, url=r.url, c=cb: self._on_override_changed(url, c))
            tw.setCellWidget(row_i, 5, cb)

        tw.resizeRowsToContents()

    def _on_override_changed(self, url: str, cb: QComboBox) -> None:
        """用户切换覆盖下拉 → 更新 config.per_url_template_overrides → 刷新 Badge。"""
        data = cb.currentData()
        ov = getattr(self._config, "per_url_template_overrides", None)
        if ov is None:
            ov = {}
            try:
                self._config.per_url_template_overrides = ov
            except Exception:  # noqa: BLE001 — dataclass 有槽/冻结时失败（CrawlConfig 是普通 dataclass）
                return
        if data == _AUTO_HINT_KEY or not data:
            ov.pop(url, None)
        else:
            ov[url] = str(data)
        # 增量刷新 Badge 文字（不重跑分类，只改 override 计数）
        overrides_count = sum(1 for v in ov.values() if v)
        base_text = self._rec_badge.text()
        # 去掉旧的 · 手动覆盖段，若有
        marker = _(" · 手动覆盖 ")
        if marker in base_text:
            base_text = base_text.split(marker)[0]
        if overrides_count:
            base_text = _("{0} · 手动覆盖 {1} 条").format(base_text, overrides_count)
        self._rec_badge.setText(base_text)
        # summary 文案末的 override 计数同步
        threshold = getattr(self, "_gate_threshold", 0.85)
        self._rec_summary_label.setText(
            _("闸门阈值 confidence ≥ {0:.2f}；L1 永远自动；fallback 兜底强制人工。"
              "每行末列可下拉切换「覆盖模板」：空=留自动，选中=强制用该模板（写入 YAML source.seed_template_overrides）。"
              "当前手动覆盖：{1} 条。").format(threshold, overrides_count)
        )

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

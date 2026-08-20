"""高级 YAML 编辑器视图。

支持语法高亮、双向同步、差异对比、自动合并和格式化。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config_model import CrawlConfig
from ..core.config_serializer import format_yaml, from_yaml, to_yaml
from ..core.validator import validate_schema
from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip

# YAML 编辑器中可用但向导无对应控件的高级设置 help_id
_YAML_ONLY_HELP_IDS = (
    "crawl.max_depth", "http.user_agent", "http.timeout",
    "browser.headless", "browser.actions", "extract.mode",
    "robots", "workspace", "schedule", "recovery", "export",
)


class YamlHighlighter(QSyntaxHighlighter):
    """YAML 语法高亮器。颜色跟随设计令牌主题。"""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._refresh_formats()
        ThemeManager.instance().theme_changed.connect(self._on_theme_changed)

    def _refresh_formats(self) -> None:
        """从设计令牌刷新高亮格式。"""
        t = ThemeManager.instance().tokens
        # 注释
        self._comment_fmt = QTextCharFormat()
        self._comment_fmt.setForeground(QColor(t.muted))
        # 键
        self._key_fmt = QTextCharFormat()
        self._key_fmt.setForeground(QColor(t.primary))
        # 字符串值
        self._string_fmt = QTextCharFormat()
        self._string_fmt.setForeground(QColor(t.success))
        # 数字/布尔
        self._value_fmt = QTextCharFormat()
        self._value_fmt.setForeground(QColor(t.warning))
        # 列表标记
        self._list_fmt = QTextCharFormat()
        self._list_fmt.setForeground(QColor(t.info))

    def _on_theme_changed(self, *_args) -> None:
        self._refresh_formats()

    def highlightBlock(self, text: str | None) -> None:
        if text is None:
            return
        # 注释
        if text.strip().startswith("#"):
            self.setFormat(0, len(text), self._comment_fmt)
            return

        # 键值对
        import re
        # 键: 值
        for match in re.finditer(r"^(\s*)([\w_-]+)(\s*:\s*)(.*?)$", text):
            indent = match.group(1)
            key = match.group(2)
            colon = match.group(3)
            value = match.group(4)

            start = match.start()
            key_start = start + len(indent)
            self.setFormat(key_start, len(key), self._key_fmt)
            self.setFormat(key_start + len(key), len(colon), self._comment_fmt)

            # 值着色
            if value.strip():
                val_start = key_start + len(key) + len(colon)
                stripped = value.strip()
                if stripped in ("true", "false", "yes", "no", "on", "off"):
                    self.setFormat(val_start, len(value), self._value_fmt)
                elif stripped.lstrip("-").isdigit() or re.match(r"^\d+\.?\d*$", stripped):
                    self.setFormat(val_start, len(value), self._value_fmt)
                else:
                    self.setFormat(val_start, len(value), self._string_fmt)

        # 列表项
        for match in re.finditer(r"^\s*-\s+", text):
            self.setFormat(match.start(), match.end() - match.start(), self._list_fmt)


class DiffDialog(QDialog):
    """差异对比对话框。

    列出编辑器与表单之间的所有差异字段，提供四种操作模式。
    """

    def __init__(
        self,
        diffs: list[tuple[str, str, str]],  # (key, editor_value, form_value)
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("配置差异对比"))
        self.setMinimumSize(700, 400)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            _("以下字段在编辑器和表单中存在差异，请选择如何处理:")
        ))

        self._table = QTableWidget(len(diffs), 4)
        self._table.setHorizontalHeaderLabels([
            _("字段"), _("编辑器值"), _("表单值"), _("采用")
        ])
        header = self._table.horizontalHeader()
        assert header is not None
        header.setStretchLastSection(False)
        self._table.setColumnWidth(2, 160)

        for i, (key, editor_val, form_val) in enumerate(diffs):
            self._table.setItem(i, 0, QTableWidgetItem(key))
            self._table.setItem(i, 1, QTableWidgetItem(editor_val))
            self._table.setItem(i, 2, QTableWidgetItem(form_val))
            choice = QTableWidgetItem(_("表单"))
            self._table.setItem(i, 3, choice)

        self._table.cellClicked.connect(self._toggle_choice)
        layout.addWidget(self._table)

        # 批量操作按钮
        btn_layout = QHBoxLayout()

        auto_merge_btn = QPushButton(_("自动合并（推荐）"))
        auto_merge_btn.clicked.connect(self._auto_merge)
        btn_layout.addWidget(auto_merge_btn)

        all_editor_btn = QPushButton(_("全部选编辑器"))
        all_editor_btn.clicked.connect(lambda: self._set_all(_("编辑器")))
        btn_layout.addWidget(all_editor_btn)

        all_form_btn = QPushButton(_("全部选表单"))
        all_form_btn.clicked.connect(lambda: self._set_all(_("表单")))
        btn_layout.addWidget(all_form_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 确认按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._diffs = diffs
        self._choices: dict[str, str] = {key: "form" for key, _, _ in diffs}

    def get_choices(self) -> dict[str, str]:
        """返回每个字段的选择 ('editor' 或 'form')。"""
        return dict(self._choices)

    def _toggle_choice(self, row: int, col: int) -> None:
        """切换选择。"""
        if col != 3:
            return
        choice_item = self._table.item(row, 3)
        assert choice_item is not None
        current = choice_item.text()
        new_val = _("编辑器") if current == _("表单") else _("表单")
        choice_item.setText(new_val)
        key_item = self._table.item(row, 0)
        assert key_item is not None
        key = key_item.text()
        if key in self._choices:
            self._choices[key] = "editor" if new_val == _("编辑器") else "form"

    def _auto_merge(self) -> None:
        """自动合并：新增字段用编辑器值，冲突以表单为准。"""
        for row in range(self._table.rowCount()):
            editor_item = self._table.item(row, 1)
            assert editor_item is not None
            editor_item.text()
            form_item = self._table.item(row, 2)
            assert form_item is not None
            form_val = form_item.text()
            choice = _("表单") if form_val else _("编辑器")
            choice_item = self._table.item(row, 3)
            assert choice_item is not None
            choice_item.setText(choice)
            key_item = self._table.item(row, 0)
            assert key_item is not None
            key = key_item.text()
            if key in self._choices:
                self._choices[key] = "form" if form_val else "editor"

    def _set_all(self, choice: str) -> None:
        """全部采用某种值。"""
        for row in range(self._table.rowCount()):
            choice_item = self._table.item(row, 3)
            assert choice_item is not None
            choice_item.setText(choice)
            key_item = self._table.item(row, 0)
            assert key_item is not None
            key = key_item.text()
            if key in self._choices:
                self._choices[key] = "editor" if choice == _("编辑器") else "form"


class YamlEditor(QWidget):
    """高级 YAML 编辑器视图。

    功能：
    - 语法高亮
    - 双向同步（防抖 300ms/500ms）
    - 差异对比与自动合并
    - 一键格式化
    - Schema 校验
    - 外部文件修改检测
    """

    config_changed = Signal()  # 编辑器内容变更后
    sync_to_form = Signal(object)  # 同步到表单 (CrawlConfig)
    sync_status = Signal(str)  # 状态栏消息

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        self._config: CrawlConfig | None = None
        self._filepath: Path | None = None
        self._last_mtime: float = 0.0
        self._external_check_timer = QTimer(self)
        self._external_check_timer.timeout.connect(self._check_external_change)
        self._external_check_timer.setInterval(2000)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏
        toolbar = QHBoxLayout()

        self._sync_to_form_btn = QPushButton(_("→ 同步到表单"))
        self._sync_to_form_btn.clicked.connect(self._do_sync_to_form)
        self._sync_to_form_btn.setToolTip(_("将编辑器内容解析后同步到向导表单"))
        toolbar.addWidget(self._sync_to_form_btn)

        self._diff_btn = QPushButton(_("显示差异"))
        self._diff_btn.clicked.connect(self._show_diff)
        self._diff_btn.setEnabled(False)
        toolbar.addWidget(self._diff_btn)

        format_btn = QPushButton(_("格式化"))
        format_btn.clicked.connect(self._format_yaml)
        toolbar.addWidget(format_btn)

        open_external_btn = QPushButton(_("系统编辑器打开"))
        open_external_btn.clicked.connect(self._open_external)
        toolbar.addWidget(open_external_btn)

        toolbar.addStretch()

        self._status_label = QLabel(_("就绪"))
        self._status_label.setObjectName("muted")
        toolbar.addWidget(self._status_label)

        layout.addLayout(toolbar)

        # 高级设置帮助引用行（YAML 专属字段的 HelpTooltip 绑定）
        help_row = QHBoxLayout()
        help_row.setContentsMargins(0, 0, 0, 0)
        help_row.addWidget(QLabel(_("YAML 字段帮助:")))
        for help_id in _YAML_ONLY_HELP_IDS:
            help_row.addWidget(HelpTooltip(help_id))
        help_row.addStretch()
        layout.addLayout(help_row)

        # 编辑器
        self._editor = QPlainTextEdit()
        self._editor.setObjectName("yamlCodeEditor")
        self._editor.setProperty("codeEditor", True)
        self._editor.setFont(QFont(FONT_FAMILY_MONO.split(", ")[0], 11))
        self._editor.textChanged.connect(self._on_editor_changed)
        self._highlighter = YamlHighlighter(self._editor.document())
        self._apply_editor_style()
        ThemeManager.instance().theme_changed.connect(self._apply_editor_style)
        layout.addWidget(self._editor)

        # 防抖定时器
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._try_sync_from_editor)

    # ---- 公共 API ----

    def set_config(self, config: CrawlConfig) -> None:
        """从配置对象更新编辑器内容。"""
        self._config = config
        self._update_editor_from_config()

    def get_config(self) -> CrawlConfig | None:
        """获取当前编辑器中的配置。"""
        try:
            return from_yaml(self._editor.toPlainText())
        except Exception:
            return None

    def update_from_config(self, config: CrawlConfig) -> None:
        """从表单配置同步到编辑器（由表单变更触发）。"""
        if self._updating:
            return
        self._updating = True
        self._config = config
        self._update_editor_from_config()
        self._updating = False

    def load_file(self, filepath: Path) -> bool:
        """加载 YAML 文件。"""
        try:
            yaml_str = filepath.read_text(encoding="utf-8")
            self._editor.setPlainText(yaml_str)
            self._filepath = filepath
            self._last_mtime = filepath.stat().st_mtime if filepath.is_file() else 0.0
            self._external_check_timer.start()
            self.config_changed.emit()
            return True
        except Exception as e:
            QMessageBox.critical(self, _("加载失败"), str(e))
            return False

    def set_yaml_text(self, text: str) -> None:
        """直接设置 YAML 文本。"""
        if self._updating:
            return
        self._updating = True
        current = self._editor.toPlainText()
        if current != text:
            cursor = self._editor.textCursor()
            self._editor.setPlainText(text)
            self._editor.setTextCursor(cursor)
        self._updating = False

    # ---- 内部方法 ----

    def _update_editor_from_config(self) -> None:
        """从 CrawlConfig 生成 YAML 并更新编辑器。"""
        if self._config is None:
            return
        try:
            yaml_str = to_yaml(self._config)
            self._editor.setPlainText(yaml_str)
        except Exception as e:
            self.sync_status.emit(_(f"生成 YAML 失败: {e}"))

    def _on_editor_changed(self) -> None:
        """编辑器内容变更处理。"""
        if self._updating:
            return
        self._sync_timer.start(500)  # 500ms 防抖

    def _try_sync_from_editor(self) -> None:
        """尝试从编辑器同步到表单。"""
        yaml_text = self._editor.toPlainText()
        try:
            config = from_yaml(yaml_text)
            errors, warnings = validate_schema(
                {"project": {"name": config.project_name, "workspace": config.workspace},
                 "source": {"kind": config.source_kind, "seeds": config.seed_urls}}
            )
            if errors:
                for err in errors:
                    self._set_editor_error_style()
                    self.sync_status.emit(_(f"YAML 校验失败: {err}"))
                return

            # 成功：恢复正常样式
            self._apply_editor_style()
            self.sync_status.emit(_("已同步"))
            self._config = config
            self.sync_to_form.emit(config)

        except Exception as e:
            self._set_editor_error_style()
            self.sync_status.emit(_(f"YAML 解析错误: {e}"))

    def _do_sync_to_form(self) -> None:
        """手动触发同步到表单。"""
        self._try_sync_from_editor()

    def _apply_editor_style(self, *_args) -> None:
        """从设计令牌生成编辑器正常样式，自动跟随主题。"""
        t = ThemeManager.instance().tokens
        self._editor.setStyleSheet(f"""
            QPlainTextEdit#yamlCodeEditor {{
                background-color: {t.code_bg};
                color: {t.code_fg};
                border: 1px solid {t.code_border};
                border-radius: {RADIUS["sm"]}px;
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
        """)

    def _set_editor_error_style(self) -> None:
        """编辑器错误状态样式（使用 danger 令牌）。"""
        t = ThemeManager.instance().tokens
        self._editor.setStyleSheet(f"""
            QPlainTextEdit#yamlCodeEditor {{
                background-color: {t.danger_bg};
                color: {t.code_fg};
                border: 2px solid {t.danger};
                border-radius: {RADIUS["sm"]}px;
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
        """)

    def _show_diff(self) -> None:
        """显示编辑器与表单的差异。"""
        if self._config is None:
            return

        try:
            editor_config = from_yaml(self._editor.toPlainText())
        except Exception:
            QMessageBox.warning(self, _("无法对比"), _("编辑器中的 YAML 格式无效，无法对比差异。"))
            return

        diffs = self._compute_diffs(self._config, editor_config)
        if not diffs:
            QMessageBox.information(self, _("无差异"), _("编辑器和表单配置完全一致。"))
            return

        dialog = DiffDialog(diffs, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            choices = dialog.get_choices()
            self._apply_choices(choices)

    def _compute_diffs(
        self, form_config: CrawlConfig, editor_config: CrawlConfig
    ) -> list[tuple[str, str, str]]:
        """计算两个配置之间的差异。"""
        diffs: list[tuple[str, str, str]] = []

        def _add(key: str, fv: str, ev: str) -> None:
            if fv != ev:
                diffs.append((key, ev, fv))

        _add(_("项目名"), form_config.project_name, editor_config.project_name)
        _add(_("工作区"), form_config.workspace, editor_config.workspace)
        _add(_("网站类型"), form_config.source_kind, editor_config.source_kind)
        _add(_("种子 URL"), "\n".join(form_config.seed_urls), "\n".join(editor_config.seed_urls))
        _add(_("最大页数"), str(form_config.max_pages), str(editor_config.max_pages))
        _add(_("请求延迟"), str(form_config.delay), str(editor_config.delay))
        _add(_("并发数"), str(form_config.concurrency), str(editor_config.concurrency))
        _add(_("用户代理"), form_config.user_agent, editor_config.user_agent)
        _add(_("下载开关"), str(form_config.download.enabled), str(editor_config.download.enabled))
        _add(_("文件扩展名"), ", ".join(form_config.download.extensions),
             ", ".join(editor_config.download.extensions))
        _add(_("字段数"), str(len(form_config.fields)), str(len(editor_config.fields)))

        return diffs

    def _apply_choices(self, choices: dict[str, str]) -> None:
        """应用差异选择结果：逐字段合并，尊重用户在每个字段上的选择。"""
        if not self._config:
            return
        try:
            editor_config = from_yaml(self._editor.toPlainText())
        except Exception:
            editor_config = self._config

        # 逐字段应用选择
        if choices.get(_("项目名")) == "editor":
            self._config.project_name = editor_config.project_name
        if choices.get(_("工作区")) == "editor":
            self._config.workspace = editor_config.workspace
        if choices.get(_("网站类型")) == "editor":
            self._config.source_kind = editor_config.source_kind
        if choices.get(_("种子 URL")) == "editor":
            self._config.seed_urls = editor_config.seed_urls
        if choices.get(_("最大页数")) == "editor":
            self._config.max_pages = editor_config.max_pages
        if choices.get(_("请求延迟")) == "editor":
            self._config.delay = editor_config.delay
        if choices.get(_("并发数")) == "editor":
            self._config.concurrency = editor_config.concurrency
        if choices.get(_("用户代理")) == "editor":
            self._config.user_agent = editor_config.user_agent
        if choices.get(_("下载开关")) == "editor":
            self._config.download.enabled = editor_config.download.enabled
        if choices.get(_("文件扩展名")) == "editor":
            self._config.download.extensions = editor_config.download.extensions
        if choices.get(_("字段数")) == "editor":
            self._config.fields = editor_config.fields

        editor_count = sum(1 for v in choices.values() if v == "editor")
        self._update_editor_from_config()
        self.sync_to_form.emit(self._config)
        if editor_count > 0:
            self.sync_status.emit(_("已按选择合并字段"))
        else:
            self.sync_status.emit(_("已采用表单值"))

    def _format_yaml(self) -> None:
        """格式化 YAML。"""
        try:
            formatted = format_yaml(self._editor.toPlainText())
            self._editor.setPlainText(formatted)
            self.sync_status.emit(_("已格式化"))
        except Exception as e:
            self.sync_status.emit(_(f"格式化失败: {e}"))

    def _open_external(self) -> None:
        """在系统编辑器中打开。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if self._filepath and self._filepath.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._filepath)))
            self._last_mtime = self._filepath.stat().st_mtime
        else:
            # A24：临时文件写系统临时目录，避免污染 cwd
            import tempfile
            temp_path = Path(tempfile.gettempdir()) / "omnicrawler_temp_config.yaml"
            temp_path.write_text(self._editor.toPlainText(), encoding="utf-8")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(temp_path)))

    def _check_external_change(self) -> None:
        """检查外部文件是否被修改。"""
        if not self._filepath or not self._filepath.is_file():
            return
        try:
            mtime = self._filepath.stat().st_mtime
            if mtime != self._last_mtime:
                self._last_mtime = mtime
                reply = QMessageBox.question(
                    self, _("文件已修改"),
                    _("配置文件已被外部程序修改，是否重新加载？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.load_file(self._filepath)
        except Exception:
            pass

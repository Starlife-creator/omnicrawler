"""Low-friction desktop home and quick-task entry point."""

from __future__ import annotations

import importlib.metadata
import logging
import math

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..services.natural_language_task import compile_natural_language
from ..services.ux_service import QuickTaskDraft, draft_quick_task
from .design_system import ThemeManager, rgba_token_to_qcolor
from .i18n import _
from .motion_signal import MotionSignal

logger = logging.getLogger(__name__)


class _AIEnrichWorker(QThread):
    """后台线程：调用 AI 增强自然语言解析结果。

    失败不影响主流程 — 本地解析结果始终先展示。
    """

    result_ready = pyqtSignal(object)  # NaturalLanguageDraft | None

    def __init__(self, request: str, parent: QWidget | None = None, project_root: str | None = None) -> None:
        super().__init__(parent)
        self._request = request
        self._project_root = project_root

    def run(self) -> None:
        try:
            from ..services.natural_language_task import compile_with_ai

            # 尝试加载 AI provider
            provider = self._load_provider()
            if provider is None:
                self.result_ready.emit(None)
                return

            result = compile_with_ai(self._request, provider)
            self.result_ready.emit(result)
        except Exception:
            logger.debug("AI enrichment failed", exc_info=True)
            self.result_ready.emit(None)
        finally:
            # S1.1.5：任务运行完立即释放，避免关闭窗口时 QThread 仍在运行
            self.deleteLater()

    def _load_provider(self) -> object | None:
        """从单一真源构造 AI provider（含 Egress 审计；未启用返回 None）。"""
        from ..services.ai_providers import provider_from_env

        return provider_from_env(project_root=self._project_root)


def _package_version() -> str:
    """动态读取包版本号。"""
    try:
        return importlib.metadata.version("omnicrawl-platform")
    except Exception:
        # A16：回退用 omnicrawl.__version__，不再硬编码 "2.7"
        from .. import __version__
        return __version__


class AmbientHero(QWidget):
    """Soft branded background with subtle optional motion and no external dependency."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(132)
        self._phase = 0.0
        app = QApplication.instance()
        assert app is not None
        self._reduced_motion = app.property("omnicrawlReducedMotion") or False
        MotionSignal.instance().reduced_motion_changed.connect(
            lambda v: setattr(self, "_reduced_motion", v)
        )
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(50)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 220, 18)
        eyebrow = QLabel(f"OMNICRAWLER {_package_version()} · DESKTOP PROFESSIONAL")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel(_("把网页、PDF 和接口变成可复核的数据"))
        title.setObjectName("homeTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        subtitle = QLabel(_("从一个地址开始。自动设置会解释原因，全量运行前始终先试跑。"))
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        if event is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        base = palette.base().color()
        accent = palette.highlight().color()
        gradient.setColorAt(0, base)
        wash = QColor(accent)
        wash.setAlpha(48)
        gradient.setColorAt(1, wash)
        painter.setPen(QColor(0, 0, 0, 0))
        painter.setBrush(gradient)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 18, 18)
        for index, radius in enumerate((72, 46, 24)):
            glow = QColor(accent)
            glow.setAlpha(18 + index * 8)
            painter.setBrush(glow)
            x = self.width() - 95 + math.sin(self._phase + index) * 9
            y = 32 + index * 30 + math.cos(self._phase + index) * 6
            painter.drawEllipse(int(x - radius), int(y - radius), radius * 2, radius * 2)
        painter.end()

    def _advance(self) -> None:
        """Tick the glow animation phase. Skips update() when reduced_motion is on."""
        if self._reduced_motion:
            return
        self._phase += 0.02
        self.update()


class HomePage(QWidget):
    quick_task_ready = pyqtSignal(object)
    natural_task_ready = pyqtSignal(object)
    open_wizard = pyqtSignal()
    open_recent = pyqtSignal()
    open_results = pyqtSignal()
    open_schedule = pyqtSignal()
    import_task = pyqtSignal()
    run_doctor = pyqtSignal()
    create_demo = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, project_root: str | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self.setObjectName("homePage")
        self.setAccessibleName(_("OmniCrawler 首页"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(14)
        layout.addWidget(AmbientHero())

        card = QFrame()
        card.setObjectName("quickTaskCard")
        card.setProperty("card", True)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 7)
        shadow.setColor(rgba_token_to_qcolor(ThemeManager.instance().tokens.card_shadow))
        card.setGraphicsEffect(shadow)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.addWidget(QLabel(_("先描述你的任务")))
        description_hint = QLabel(
            _("用一句话说明你想采集什么、范围有多大、是否下载文件或监测变化。")
            + _("系统会生成可修改的安全草案，并始终先试跑。")
        )
        description_hint.setWordWrap(True)
        description_hint.setObjectName("muted")
        card_layout.addWidget(description_hint)
        self.natural_language = QPlainTextEdit()
        self.natural_language.setPlaceholderText(
            _("描述你的任务，例如：分析合同 PDF 中的金额和日期，或每周监测某网站的动态")
        )
        self.natural_language.setAccessibleName(_("自然语言任务描述"))
        self.natural_language.setMinimumHeight(82)
        self.natural_language.setMaximumHeight(130)
        card_layout.addWidget(self.natural_language)
        natural_actions = QHBoxLayout()
        nl_button = QPushButton(_("生成安全草案"))
        nl_button.setProperty("primary", True)
        nl_button.clicked.connect(self._draft_natural_language)
        natural_actions.addWidget(nl_button)
        natural_actions.addStretch()
        card_layout.addLayout(natural_actions)

        url_hint = QLabel(_("或者只填写网址并选择任务类型："))
        url_hint.setObjectName("muted")
        card_layout.addWidget(url_hint)
        self.url = QLineEdit()
        self.url.setPlaceholderText(_("粘贴网页地址，例如 https://example.com/news"))
        self.url.setAccessibleName(_("任务入口网址"))
        self.url.setClearButtonEnabled(True)
        card_layout.addWidget(self.url)
        # 最近使用的 URL 下拉
        recent_row = QHBoxLayout()
        recent_row.addWidget(QLabel(_("最近:")))
        self.recent_combo = QComboBox()
        self.recent_combo.setMinimumWidth(200)
        self.recent_combo.setAccessibleName(_("最近使用的网址"))
        self.recent_combo.currentTextChanged.connect(self._on_recent_selected)
        recent_row.addWidget(self.recent_combo)
        recent_row.addStretch()
        card_layout.addLayout(recent_row)
        self._load_recent_urls()
        intent_row = QHBoxLayout()
        self.intent_group = QButtonGroup(self)
        options = (
            (_("保存这个页面"), "save_page"), (_("采集整个栏目"), "collect_section"),
            (_("下载附件/PDF"), "download_files"), (_("监测内容变化"), "monitor_changes"),
        )
        for index, (label, value) in enumerate(options):
            button = QRadioButton(label)
            button.setProperty("intent", value)
            button.setAccessibleName(_(f"任务类型：{label}"))
            self.intent_group.addButton(button)
            intent_row.addWidget(button)
            if index == 0:
                button.setChecked(True)
        card_layout.addLayout(intent_row)
        action_row = QHBoxLayout()
        self.analyse_button = QPushButton(_("分析并准备试跑"))
        self.analyse_button.setAccessibleName(_("分析网址并准备试跑"))
        self.analyse_button.setProperty("primary", True)
        self.analyse_button.clicked.connect(self._draft_quick)
        action_row.addWidget(self.analyse_button)
        edit = QPushButton(_("进入完整五步向导"))
        edit.clicked.connect(self.open_wizard.emit)
        action_row.addWidget(edit)
        action_row.addStretch()
        card_layout.addLayout(action_row)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName(_("快速任务分析结果"))
        card_layout.addWidget(self.feedback)
        layout.addWidget(card)

        grid = QGridLayout()
        actions = (
            (_("新建任务"), self.open_wizard.emit), (_("最近任务"), self.open_recent.emit),
            (_("定时监测"), self.open_schedule.emit), (_("结果与复核"), self.open_results.emit),
            (_("导入任务"), self.import_task.emit), (_("系统体检"), self.run_doctor.emit),
            (_("5分钟离线演示"), self.create_demo.emit),
        )
        for index, (label, callback) in enumerate(actions):
            action_btn = QPushButton(label)
            action_btn.setProperty("homeAction", True)
            action_btn.setMinimumHeight(42)
            action_btn.setAccessibleName(label)
            action_btn.clicked.connect(callback)
            grid.addWidget(action_btn, index // 4, index % 4)
        layout.addLayout(grid)
        layout.addStretch()

    def _draft_quick(self) -> None:
        try:
            selected = self.intent_group.checkedButton()
            intent = str(selected.property("intent")) if selected else "save_page"
            draft = draft_quick_task(self.url.text(), intent)
        except ValueError as exc:
            self.feedback.setText(_(f"请修改：{exc}"))
            return
        self._save_recent_url(self.url.text())
        self._show_draft(draft)

    def _draft_natural_language(self) -> None:
        """三层判定处理自然语言输入：URL → 文件路径 → 二选一对话框。"""
        request = self.natural_language.toPlainText().strip()
        if not request:
            self.feedback.setText(_("请描述你的任务"))
            return
        try:
            compiled = compile_natural_language(request, fallback_url=self.url.text().strip())
        except ValueError as exc:
            self.feedback.setText(_(f"请补充：{exc}"))
            return

        # Layer 3: 都没命中 → 二选一对话框
        if compiled.mode == "ambiguous":
            self._show_mode_dialog(request)
            return

        if compiled.mode == "pdf":
            self._handle_pdf_mode(compiled)
            return

        # crawl 模式（现有逻辑）
        self.url.setText(compiled.task.url)
        self._save_recent_url(compiled.task.url)
        self._show_draft(compiled.task, emit=False)
        self.natural_task_ready.emit(compiled)
        self._try_ai_enrich(compiled)

    def _show_mode_dialog(self, request: str) -> None:
        """弹出二选一对话框，让用户选择爬虫还是 PDF 模式。"""
        msg = QMessageBox(self)
        msg.setWindowTitle(_("选择任务类型"))
        msg.setText(_("无法自动判断你的意图。\n你想做什么？"))
        crawl_btn = msg.addButton(_("爬取网页数据"), QMessageBox.ButtonRole.AcceptRole)
        pdf_btn = msg.addButton(_("处理本地文件（PDF/文档）"), QMessageBox.ButtonRole.ActionRole)
        msg.addButton(_("取消"), QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == crawl_btn:
            # 用户选择爬虫 → 弹出 URL 输入
            self.url.setFocus()
            self.feedback.setText(_("请在下方输入目标网址后点击「分析并准备试跑」"))
        elif clicked == pdf_btn:
            # 用户选择 PDF → 引导到 PDF 工作台
            self.feedback.setText(_("已切换为文件处理模式。请前往「📄 PDF 工作台」选择目录和模板。"))
            # 保存原始需求，供 PDF 工作台读取
            self.setProperty("last_nl_request", request)

    def _handle_pdf_mode(self, compiled: object) -> None:
        """处理 PDF 模式：展示检测到的文件路径和解析结果。"""
        draft = compiled  # type: ignore[assignment]
        paths_text = "\n".join(f"  • {p}" for p in draft.file_paths) if hasattr(draft, 'file_paths') and draft.file_paths else _("（未检测到具体文件路径）")
        self.feedback.setText(
            _("📄 检测为文件处理任务\n")
            + _(f"检测到的文件：\n{paths_text}\n")
            + _("请前往「📄 PDF 工作台」开始处理。")
        )
        self.setProperty("last_nl_request", draft.request)  # type: ignore[attr-defined]

    def _show_draft(self, draft: QuickTaskDraft, *, emit: bool = True) -> None:
        self.feedback.setText(_("已安全限制在入口站点；将先试跑。为什么：") + "；".join(draft.decisions))
        if emit:
            self.quick_task_ready.emit(draft)

    def _try_ai_enrich(self, compiled: object) -> None:
        """双路径：本地解析已出结果，异步启动 AI 增强。"""
        draft = compiled  # type: ignore[assignment]
        request = draft.request if hasattr(draft, 'request') else ""
        if not request:
            return

        self._enrich_worker = _AIEnrichWorker(request, self, project_root=self._project_root)
        self._enrich_worker.result_ready.connect(self._on_ai_enriched)
        self._enrich_worker.start()

    def _on_ai_enriched(self, ai_draft: object | None) -> None:
        """AI 增强结果到达：合并展示，不覆盖本地结果。"""
        if ai_draft is None:
            return

        draft = ai_draft  # type: ignore[assignment]
        parts = [self.feedback.text()]

        if hasattr(draft, 'ai_assumptions') and draft.ai_assumptions:
            parts.append(_("\n--- AI 分析 ---"))
            for a in draft.ai_assumptions[:3]:
                parts.append(_(f"  • 假设「{a.get('field', '?')}」= {a.get('value', '?')}（置信度: {a.get('confidence', '?')}）"))

        if hasattr(draft, 'ai_risks') and draft.ai_risks:
            parts.append(_("⚠ 风险提示："))
            for r in draft.ai_risks[:3]:
                parts.append(f"  • {r.get('risk', '?')} [{r.get('severity', '?')}]")

        if hasattr(draft, 'ai_recommendations') and draft.ai_recommendations:
            parts.append(_("💡 建议操作："))
            for rec in draft.ai_recommendations[:3]:
                parts.append(f"  • {rec}")

        if len(parts) > 1:
            self.feedback.setText("\n".join(parts))

    def _load_recent_urls(self) -> None:
        """从本地缓存加载最近使用的 URL。"""
        try:
            from pathlib import Path
            cache = Path.home() / ".omnicrawl_recent_urls.txt"
            if cache.exists():
                urls = [u.strip() for u in cache.read_text().split("\n") if u.strip()]
                self.recent_combo.addItem(_("— 最近使用 —"))
                for u in urls[-10:]:
                    short = u[:60] + ("…" if len(u) > 60 else "")
                    self.recent_combo.addItem(short, u)
        except Exception:
            logger.debug("Failed to load recent URLs cache", exc_info=True)

    def _save_recent_url(self, url: str) -> None:
        """保存 URL 到本地缓存。"""
        try:
            from pathlib import Path
            cache = Path.home() / ".omnicrawl_recent_urls.txt"
            existing = set()
            if cache.exists():
                existing = {u.strip() for u in cache.read_text().split("\n") if u.strip()}
            existing.add(url.strip())
            cache.write_text("\n".join(existing), encoding="utf-8")
        except Exception:
            logger.debug("Failed to save recent URL cache", exc_info=True)

    def _on_recent_selected(self, text: str) -> None:
        if text and not text.startswith("—"):
            data = self.recent_combo.currentData()
            if data:
                self.url.setText(data)

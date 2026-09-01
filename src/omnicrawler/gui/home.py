"""Low-friction desktop home and quick-task entry point."""

from __future__ import annotations

import importlib.metadata
import logging
import math

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
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

    result_ready = Signal(object)  # NaturalLanguageDraft | None
    # C13/C25：明确区分「未启用/已禁用」与「运行出错」，UI 可明示而非吞掉
    ai_unavailable = Signal(str)  # reason
    ai_error = Signal(str)  # error message

    def __init__(self, request: str, parent: QWidget | None = None, project_root: str | None = None) -> None:
        super().__init__(parent)
        self._request = request
        self._project_root = project_root

    def run(self) -> None:
        try:
            # C37：隐私闸门 — 页面文本外发被禁用时直接跳过 AI，不静默发走
            from ..core.ai_env import load_ai_privacy

            privacy = load_ai_privacy(self._project_root)
            if not privacy.get("allow_page_text", True):
                self.ai_unavailable.emit(_("AI 页面文本外发已按隐私设置禁用，已使用本地解析"))
                return

            from ..services.natural_language_task import compile_with_ai

            # 尝试加载 AI provider
            provider = self._load_provider()
            if provider is None:
                self.ai_unavailable.emit(_("AI 未启用：请在「AI 服务中心」配置后重试"))
                return

            result = compile_with_ai(self._request, provider)
            self.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001 - 错误需上抛给 UI，不再是静默 None
            # C12/C25：将异常（含 AISafetyViolation 越权拦截）明示给用户
            reason = str(exc).strip() or type(exc).__name__
            logger.debug("AI enrichment failed: %s", reason, exc_info=True)
            self.ai_error.emit(reason)
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
        return importlib.metadata.version("omnicrawler-platform")
    except Exception:
        # A16：回退用 omnicrawler.__version__，不再硬编码 "2.7"
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
        self._reduced_motion = app.property("omnicrawlerReducedMotion") or False
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
    quick_task_ready = Signal(object)
    natural_task_ready = Signal(object)
    open_workspace = Signal()
    open_recent = Signal()
    open_recent_config = Signal(str)
    open_recent_results = Signal(str)
    open_results = Signal()
    open_schedule = Signal()
    import_task = Signal()
    run_doctor = Signal()
    create_demo = Signal()
    open_convert_tool = Signal()  # 格式互转：B-4 ConvertX 面板
    open_scene = Signal()  # 场景管理：S4 场景/槽位/基因面板
    open_run_compare = Signal()  # 运行对比：review/run_compare

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
        card_layout.addWidget(QLabel(_("你想采集什么？")))
        description_hint = QLabel(
            _("粘贴网址，或用一句话说明目标、范围、下载和监测要求。")
            + _("系统会自动选择安全起点，生成可修改草案，并始终先试跑。")
        )
        description_hint.setWordWrap(True)
        description_hint.setObjectName("muted")
        card_layout.addWidget(description_hint)
        self.task_input = QPlainTextEdit()
        self.task_input.setPlaceholderText(
            _("粘贴 https://example.com/news，或描述：每周监测该网站并下载新增 PDF")
        )
        self.task_input.setAccessibleName(_("任务网址或描述"))
        self.task_input.setMinimumHeight(96)
        self.task_input.setMaximumHeight(150)
        card_layout.addWidget(self.task_input)

        examples = QHBoxLayout()
        examples.addWidget(QLabel(_("示例：")))
        for label, text in (
            (_("采集新闻标题"), _("采集新闻栏目中的标题、日期和链接")),
            (_("下载 PDF"), _("下载网页中的新增 PDF 附件")),
            (_("监测变化"), _("每周监测网页内容变化")),
        ):
            chip = QPushButton(label)
            chip.setFlat(True)
            chip.setProperty("exampleChip", True)
            chip.clicked.connect(lambda _checked=False, value=text: self.task_input.setPlainText(value))
            examples.addWidget(chip)
        examples.addStretch()
        card_layout.addLayout(examples)

        action_row = QHBoxLayout()
        self.create_button = QPushButton(_("创建任务"))
        self.create_button.setAccessibleName(_("创建任务并准备试跑"))
        self.create_button.setProperty("primary", True)
        self.create_button.clicked.connect(self._create_task)
        action_row.addWidget(self.create_button)
        edit = QPushButton(_("打开空白任务"))
        edit.clicked.connect(self.open_workspace.emit)
        action_row.addWidget(edit)
        action_row.addStretch()
        card_layout.addLayout(action_row)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName(_("快速任务分析结果"))
        card_layout.addWidget(self.feedback)
        layout.addWidget(card)

        recent_header = QHBoxLayout()
        recent_header.addWidget(QLabel(_("最近任务")))
        recent_header.addStretch()
        all_recent = QPushButton(_("查看全部"))
        all_recent.setFlat(True)
        all_recent.clicked.connect(self.open_recent.emit)
        recent_header.addWidget(all_recent)
        layout.addLayout(recent_header)
        self._recent_tasks_host = QWidget()
        self._recent_tasks_layout = QVBoxLayout(self._recent_tasks_host)
        self._recent_tasks_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_tasks_layout.setSpacing(6)
        layout.addWidget(self._recent_tasks_host)
        self.set_recent_tasks([])

        secondary = QHBoxLayout()
        for label, callback in (
            (_("打开空白任务"), self.open_workspace.emit),
            (_("导入任务"), self.import_task.emit),
            (_("5分钟离线演示"), self.create_demo.emit),
        ):
            button = QPushButton(label)
            button.setProperty("homeAction", True)
            button.clicked.connect(callback)
            secondary.addWidget(button)
        secondary.addStretch()
        layout.addLayout(secondary)
        layout.addStretch()

    def _create_task(self) -> None:
        """从统一输入框创建网址任务或自然语言任务草稿。"""
        request = self.task_input.toPlainText().strip()
        if not request:
            self.feedback.setText(_("请粘贴网址或描述你的任务"))
            return

        # 纯 URL 走最小、可预测的本地草稿；包含说明的输入交给自然语言编译器。
        if request.startswith(("http://", "https://")) and not any(char.isspace() for char in request):
            try:
                self._show_draft(draft_quick_task(request, "save_page"))
            except ValueError as exc:
                self.feedback.setText(_("请修改：{0}").format(exc))
            return

        try:
            compiled = compile_natural_language(request, fallback_url="")
        except ValueError as exc:
            self.feedback.setText(_("请补充：{0}").format(exc))
            return

        # Layer 3: 都没命中 → 二选一对话框
        if compiled.mode == "ambiguous":
            self._show_mode_dialog(request)
            return

        if compiled.mode == "pdf":
            self._handle_pdf_mode(compiled)
            return

        # crawl 模式（现有逻辑）
        self._show_draft(compiled.task, emit=False)
        self.natural_task_ready.emit(compiled)
        self._try_ai_enrich(compiled)

    # 兼容旧快捷入口；统一转入单一创建流程。
    def _draft_quick(self) -> None:
        self._create_task()

    def _draft_natural_language(self) -> None:
        self._create_task()

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
            self.task_input.setFocus()
            self.feedback.setText(_("请在任务描述中补充目标网址，然后点击“创建任务”"))
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

    def set_recent_tasks(self, records: list[dict[str, object]]) -> None:
        """刷新首页最近任务卡片；数据来自 TaskHistory 的公开快照。"""
        while self._recent_tasks_layout.count():
            item = self._recent_tasks_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not records:
            empty = QLabel(_("暂无最近任务。创建并运行一次任务后，可从这里继续编辑或查看结果。"))
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self._recent_tasks_layout.addWidget(empty)
            return

        status_names = {
            "finished": _("已完成"),
            "error": _("运行失败"),
            "running": _("运行中"),
        }
        for record in records[:4]:
            row = QFrame()
            row.setProperty("card", True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            name = str(record.get("project_name") or _("未命名任务"))
            started = str(record.get("started_at") or "")[:16].replace("T", " ")
            status = status_names.get(str(record.get("status") or ""), _("待处理"))
            summary = QLabel(f"<b>{name}</b><br><small>{status}　{started}</small>")
            row_layout.addWidget(summary, 1)

            config_path = str(record.get("config_path") or "")
            workspace = str(record.get("workspace") or "")
            edit = QPushButton(_("继续编辑"))
            edit.setEnabled(bool(config_path))
            edit.clicked.connect(
                lambda _checked=False, path=config_path: self.open_recent_config.emit(path)
            )
            row_layout.addWidget(edit)
            results = QPushButton(_("查看结果"))
            results.setEnabled(bool(workspace))
            results.clicked.connect(
                lambda _checked=False, path=workspace: self.open_recent_results.emit(path)
            )
            row_layout.addWidget(results)
            self._recent_tasks_layout.addWidget(row)

    def _try_ai_enrich(self, compiled: object) -> None:
        """双路径：本地解析已出结果，异步启动 AI 增强。"""
        draft = compiled  # type: ignore[assignment]
        request = draft.request if hasattr(draft, 'request') else ""
        if not request:
            return

        # C17：启动新 worker 前先回收旧 worker，避免覆盖旧任务仍在跑导致结果错乱
        old = getattr(self, "_enrich_worker", None)
        if old is not None:
            old.quit()
            old.wait(500)
            old.deleteLater()

        self._enrich_worker = _AIEnrichWorker(request, self, project_root=self._project_root)
        self._enrich_worker.result_ready.connect(self._on_ai_enriched)
        self._enrich_worker.ai_unavailable.connect(self._on_ai_unavailable)
        self._enrich_worker.ai_error.connect(self._on_ai_error)
        self._enrich_worker.start()

    def _on_ai_unavailable(self, reason: str) -> None:
        """C13：AI 未启用/被隐私禁用时，明确告知用户（仍保留本地解析结果）。"""
        base = self.feedback.text()
        if reason not in base:
            self.feedback.setText(_(f"{base}\nℹ {reason}（已使用本地解析）"))

    def _on_ai_error(self, message: str) -> None:
        """C12/C25：AI 运行出错（含越权拦截）时明示，而非静默丢弃。"""
        base = self.feedback.text()
        self.feedback.setText(_(f"{base}\n⚠ AI 增强失败：{message}（已使用本地解析）"))

    def _on_ai_enriched(self, ai_draft: object | None) -> None:
        """AI 增强结果到达：合并展示，不覆盖本地结果。"""
        if ai_draft is None:
            return

        draft = ai_draft  # type: ignore[assignment]
        parts = [self.feedback.text()]
        # C18：标注 AI 增强来源，避免与本地规则解析混淆
        parts.append(_("\n--- AI 增强（在线模型，非本地规则）---"))

        if hasattr(draft, 'ai_assumptions') and draft.ai_assumptions:
            for a in draft.ai_assumptions[:3]:
                parts.append(_(f"  • 假设「{a.get('field', '?')}」= {a.get('value', '?')}（置信度: {a.get('confidence', '?')}）"))

        if hasattr(draft, 'ai_risks') and draft.ai_risks:
            parts.append(_("⚠ 风险提示："))
            for r in draft.ai_risks[:3]:
                parts.append(f"  • {r.get('risk', '?')}")  # 严重度已在风险文本内

        if hasattr(draft, 'ai_recommendations') and draft.ai_recommendations:
            parts.append(_("💡 建议操作："))
            for rec in draft.ai_recommendations[:3]:
                parts.append(f"  • {rec}")

        if len(parts) > 1:
            self.feedback.setText("\n".join(parts))

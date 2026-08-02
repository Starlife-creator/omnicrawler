"""Low-friction desktop home and quick-task entry point."""

from __future__ import annotations

import logging
import math

from PyQt6.QtCore import QTimer, pyqtSignal
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
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..services.natural_language_task import compile_natural_language
from ..services.ux_service import QuickTaskDraft, draft_quick_task
from .design_system import ThemeManager, rgba_token_to_qcolor
from .motion_signal import MotionSignal

logger = logging.getLogger(__name__)


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
        eyebrow = QLabel("OMNICRAWLER 2.1 · DESKTOP PROFESSIONAL")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("把网页、PDF 和接口变成可复核的数据")
        title.setObjectName("homeTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        subtitle = QLabel("从一个地址开始。自动设置会解释原因，全量运行前始终先试跑。")
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("homePage")
        self.setAccessibleName("OmniCrawler 首页")
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
        card_layout.addWidget(QLabel("先描述你的任务"))
        description_hint = QLabel(
            "用一句话说明你想采集什么、范围有多大、是否下载文件或监测变化。"
            "系统会生成可修改的安全草案，并始终先试跑。"
        )
        description_hint.setWordWrap(True)
        description_hint.setObjectName("muted")
        card_layout.addWidget(description_hint)
        self.natural_language = QPlainTextEdit()
        self.natural_language.setPlaceholderText(
            "例如：每周监测 https://example.com/news 中“人工智能”相关内容，下载 PDF 并导出 Excel"
        )
        self.natural_language.setAccessibleName("自然语言任务描述")
        self.natural_language.setMinimumHeight(82)
        self.natural_language.setMaximumHeight(130)
        card_layout.addWidget(self.natural_language)
        natural_actions = QHBoxLayout()
        nl_button = QPushButton("生成安全草案")
        nl_button.setProperty("primary", True)
        nl_button.clicked.connect(self._draft_natural_language)
        natural_actions.addWidget(nl_button)
        natural_actions.addStretch()
        card_layout.addLayout(natural_actions)

        url_hint = QLabel("或者只填写网址并选择任务类型：")
        url_hint.setObjectName("muted")
        card_layout.addWidget(url_hint)
        self.url = QLineEdit()
        self.url.setPlaceholderText("粘贴网页地址，例如 https://example.com/news")
        self.url.setAccessibleName("任务入口网址")
        self.url.setClearButtonEnabled(True)
        card_layout.addWidget(self.url)
        # 最近使用的 URL 下拉
        recent_row = QHBoxLayout()
        recent_row.addWidget(QLabel("最近:"))
        self.recent_combo = QComboBox()
        self.recent_combo.setMinimumWidth(200)
        self.recent_combo.setAccessibleName("最近使用的网址")
        self.recent_combo.currentTextChanged.connect(self._on_recent_selected)
        recent_row.addWidget(self.recent_combo)
        recent_row.addStretch()
        card_layout.addLayout(recent_row)
        self._load_recent_urls()
        intent_row = QHBoxLayout()
        self.intent_group = QButtonGroup(self)
        options = (
            ("保存这个页面", "save_page"), ("采集整个栏目", "collect_section"),
            ("下载附件/PDF", "download_files"), ("监测内容变化", "monitor_changes"),
        )
        for index, (label, value) in enumerate(options):
            button = QRadioButton(label)
            button.setProperty("intent", value)
            button.setAccessibleName(f"任务类型：{label}")
            self.intent_group.addButton(button)
            intent_row.addWidget(button)
            if index == 0:
                button.setChecked(True)
        card_layout.addLayout(intent_row)
        action_row = QHBoxLayout()
        self.analyse_button = QPushButton("分析并准备试跑")
        self.analyse_button.setAccessibleName("分析网址并准备试跑")
        self.analyse_button.setProperty("primary", True)
        self.analyse_button.clicked.connect(self._draft_quick)
        action_row.addWidget(self.analyse_button)
        edit = QPushButton("进入完整五步向导")
        edit.clicked.connect(self.open_wizard.emit)
        action_row.addWidget(edit)
        action_row.addStretch()
        card_layout.addLayout(action_row)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setAccessibleName("快速任务分析结果")
        card_layout.addWidget(self.feedback)
        layout.addWidget(card)

        grid = QGridLayout()
        actions = (
            ("新建任务", self.open_wizard.emit), ("最近任务", self.open_recent.emit),
            ("定时监测", self.open_schedule.emit), ("结果与复核", self.open_results.emit),
            ("导入任务", self.import_task.emit), ("系统体检", self.run_doctor.emit),
            ("5分钟离线演示", self.create_demo.emit),
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
            self.feedback.setText(f"请修改：{exc}")
            return
        self._save_recent_url(self.url.text())
        self._show_draft(draft)

    def _draft_natural_language(self) -> None:
        try:
            compiled = compile_natural_language(self.natural_language.toPlainText())
        except ValueError as exc:
            self.feedback.setText(f"请补充：{exc}")
            return
        self.url.setText(compiled.task.url)
        self._save_recent_url(compiled.task.url)
        self._show_draft(compiled.task, emit=False)
        self.natural_task_ready.emit(compiled)

    def _show_draft(self, draft: QuickTaskDraft, *, emit: bool = True) -> None:
        self.feedback.setText("已安全限制在入口站点；将先试跑。为什么：" + "；".join(draft.decisions))
        if emit:
            self.quick_task_ready.emit(draft)

    def _load_recent_urls(self) -> None:
        """从本地缓存加载最近使用的 URL。"""
        try:
            from pathlib import Path
            cache = Path.home() / ".omnicrawl_recent_urls.txt"
            if cache.exists():
                urls = [u.strip() for u in cache.read_text().split("\n") if u.strip()]
                self.recent_combo.addItem("— 最近使用 —")
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

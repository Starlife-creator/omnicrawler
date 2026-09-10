"""TaskCanvas 的「意图区 / 探测 / 新手引导」域 —— 由 task_canvas.py 抽出的 Mixin（P1-3 第四批）。

涵盖 ① 意图区：
- ``_build_intent_area``：URL + 一句话描述 + 新手引导卡片（onboarding）
- URL 预探测：``_on_intent_changed`` / ``_fire_probe`` / ``set_probe_result`` /
  ``set_probe_failed`` / ``_set_probe_badge``（防抖 ``_probe_timer`` 由宿主创建）
- P3 首启引导气泡与引导清单：``maybe_show_welcome_tip`` / ``_dismiss_welcome_tip`` /
  ``_update_onboarding`` / ``_dismiss_onboarding``

以 Mixin 形式保留 ``self`` 语义，宿主属性结构（五区 section 等）与调用点完全不变。

注：``probe_requested`` 是 Qt Signal，**必须留在宿主**（Signal 依赖 QObject 元类，
纯 mixin 无法承载），此处只做类型声明；``_PROBE_DEBOUNCE_MS`` 等类级常量同理只在宿主定义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton

from ..design_system import SPACING
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip
from .task_canvas_components import _repolish_widget

if TYPE_CHECKING:
    from PySide6.QtCore import QTimer

    from ..core.config_model import CrawlConfig
    from .task_canvas_components import _Section


class IntentAreaMixin:
    """① 意图区构建 + URL 预探测 + 新手引导。"""

    # ---- 宿主契约：实例属性（TaskCanvas 在 __init__ / 各 _build_* 中创建）----
    _config: CrawlConfig
    _locked: bool
    _trial_ok: bool
    _intent_section: _Section
    _url_edit: QLineEdit
    _url_badge: QLabel
    _desc_edit: QLineEdit
    _start_btn: QPushButton
    _probe_badge: QLabel
    _probe_timer: QTimer
    _welcome_tip: QLabel
    _welcome_timer: QTimer
    _onboarding_card: QFrame
    _onboarding_text: QLabel
    probe_requested: Any  # Qt Signal（定义在宿主 TaskCanvas 上）

    # ---- 宿主契约：TaskCanvas 类级常量 ----
    _PROBE_DEBOUNCE_MS: int
    _WELCOME_TIP_MS: int
    _WELCOME_TIP_KEY: str
    _ONBOARDING_KEY: str

    if TYPE_CHECKING:
        # 由 TaskCanvas / 其他 Mixin 提供；仅类型检查期可见，运行期不存在，故不遮蔽宿主实现。
        @staticmethod
        def _is_valid_url(url: str) -> bool: ...
        def _on_scope_changed(self, *_args: Any) -> None: ...
        def _on_start(self) -> None: ...  # 作为 clicked 槽被裸引用，非直接调用

    # ------------------------------------------------------------------
    #  ① 意图区
    # ------------------------------------------------------------------
    def _build_intent_area(self) -> None:
        body = self._intent_section.body()
        self._onboarding_card = QFrame()
        self._onboarding_card.setObjectName("onboardingCard")
        onboarding_layout = QHBoxLayout(self._onboarding_card)
        onboarding_layout.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"])
        self._onboarding_text = QLabel("")
        self._onboarding_text.setWordWrap(True)
        onboarding_layout.addWidget(self._onboarding_text, 1)
        onboarding_skip = QPushButton(_("跳过引导"))
        onboarding_skip.setFlat(True)
        onboarding_skip.clicked.connect(self._dismiss_onboarding)
        onboarding_layout.addWidget(onboarding_skip)
        self._onboarding_card.setVisible(False)
        body.addWidget(self._onboarding_card)

        # P3：首启引导气泡（默认隐藏；maybe_show_welcome_tip 触发）
        self._welcome_tip = QLabel("")
        self._welcome_tip.setObjectName("welcomeTip")
        self._welcome_tip.setVisible(False)
        body.addWidget(self._welcome_tip)

        url_row = QHBoxLayout()
        url_row.addWidget(HelpTooltip("source.seed"))
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(_("试试粘贴一个网址开始，例如 https://example.org/news"))
        self._url_edit.textChanged.connect(self._on_intent_changed)
        url_row.addWidget(self._url_edit)
        body.addLayout(url_row)

        desc_row = QHBoxLayout()
        desc_row.addWidget(HelpTooltip("task.intent"))
        desc_row.addWidget(HelpTooltip("ai.mode"))
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText(_("（可选）一句话描述任务，如：采集新闻标题并监测变化"))
        self._desc_edit.setClearButtonEnabled(True)
        self._desc_edit.textChanged.connect(self._on_intent_changed)
        desc_row.addWidget(self._desc_edit)
        body.addLayout(desc_row)

        # P2：URL 探活反馈行（可访问性 / 静态或动态 / 分页线索；失败静默降级）
        probe_row = QHBoxLayout()
        self._probe_badge = QLabel("")
        self._probe_badge.setObjectName("muted")
        self._probe_badge.setWordWrap(True)
        probe_row.addWidget(self._probe_badge)
        probe_row.addStretch()
        body.addLayout(probe_row)

        row = QHBoxLayout()
        self._url_badge = QLabel("")
        self._url_badge.setObjectName("badge")
        row.addWidget(self._url_badge)
        row.addStretch()
        self._start_btn = QPushButton(_("开始"))
        self._start_btn.setProperty("primary", True)
        self._start_btn.setEnabled(False)
        self._start_btn.setToolTip(_("需要有效网址"))
        self._start_btn.clicked.connect(self._on_start)
        row.addWidget(self._start_btn)
        body.addLayout(row)

    def _on_intent_changed(self) -> None:
        url = self._url_edit.text().strip()
        valid = bool(url) and self._is_valid_url(url)
        # P3：用户开始输入即提前关闭首启气泡（不打扰）
        if url:
            self._dismiss_welcome_tip()
        self._start_btn.setEnabled(valid and not self._locked)
        self._start_btn.setToolTip(_("需要有效网址") if not valid else _("根据网址生成任务草稿"))
        self._url_badge.setText("")
        if url:
            self._url_badge.setText(_("✓ 已识别网址") if valid else _("⚠ 网址格式不完整"))
            _repolish_widget(self._url_badge)
        # P2：URL 有效且未锁定时调度探活（600ms 防抖）；否则取消挂起探测并清空反馈
        if valid and not self._locked:
            self._probe_timer.start(self._PROBE_DEBOUNCE_MS)
        else:
            self._probe_timer.stop()
            self._set_probe_badge("")
        self._on_scope_changed()
        self._update_onboarding()

    def _fire_probe(self) -> None:
        """防抖期满：意图区当前 URL 仍有效才发起探活（锁定态/输入变更时不发）。"""
        url = self._url_edit.text().strip()
        if self._locked or not url or not self._is_valid_url(url):
            return
        self._set_probe_badge(_("正在探测…"))
        self.probe_requested.emit(url)

    def set_probe_result(self, url: str, report: dict | None) -> None:
        """探活结果回填徽标；URL 已变更则视为过期结果，丢弃不显示。"""
        if url != self._url_edit.text().strip() or report is None:
            return
        page_type = {
            "list": _("列表页"),
            "detail": _("详情页"),
            "search": _("搜索页"),
            "unknown": _("结构未知"),
        }.get(str(report.get("page_type", "unknown")), _("结构未知"))
        kind = _("动态页面") if report.get("dynamic") else _("静态页面")
        pagination = bool(report.get("pagination"))
        pagination_text = _("含分页线索") if pagination else _("无分页线索")
        self._set_probe_badge(
            _("✓ 可访问 · %(page)s · %(kind)s · %(pagination)s") % {
                "page": page_type, "kind": kind, "pagination": pagination_text,
            }
        )

    def set_probe_failed(self, url: str, _message: str) -> None:
        """探活失败静默降级：仅提示可手动配置，绝不阻断主流程。"""
        if url != self._url_edit.text().strip():
            return
        self._set_probe_badge(_("探测不可用（网络受限或站点拒绝自动访问）；可继续手动配置"))

    def _set_probe_badge(self, text: str) -> None:
        self._probe_badge.setText(text)
        _repolish_widget(self._probe_badge)

    # ------------------------------------------------------------------
    #  P3：首启引导气泡（PRD §3.1）——最克制：非弹窗、3 秒消失、不重复
    # ------------------------------------------------------------------
    def maybe_show_welcome_tip(self) -> None:
        """首次打开画布且无草稿时，输入框短暂高亮 + 气泡提示（3 秒自动消失）。

        已看过（本地偏好）或画布已有草稿则跳过；关闭后不再重复。
        """
        from ...gui.settings import make_qsettings

        settings = make_qsettings("OmniCrawler", "GUIWorkbench")
        if not settings.value(self._ONBOARDING_KEY, False, type=bool):
            self._onboarding_card.setVisible(True)
            self._update_onboarding()
        if self._config.seed_urls:
            return
        if settings.value(self._WELCOME_TIP_KEY, False, type=bool):
            return
        self._welcome_tip.setText(_("💡 试试粘贴一个网址开始"))
        self._welcome_tip.setVisible(True)
        self._url_edit.setProperty("welcomeHighlight", True)
        _repolish_widget(self._url_edit)
        self._welcome_timer.start(self._WELCOME_TIP_MS)

    def _dismiss_welcome_tip(self) -> None:
        """关闭气泡 + 取消高亮 + 记录本地偏好（不再重复）。"""
        self._welcome_timer.stop()
        self._welcome_tip.setVisible(False)
        if self._url_edit.property("welcomeHighlight"):
            self._url_edit.setProperty("welcomeHighlight", False)
            _repolish_widget(self._url_edit)
        from ...gui.settings import make_qsettings

        settings = make_qsettings("OmniCrawler", "GUIWorkbench")
        settings.setValue(self._WELCOME_TIP_KEY, True)

    def _update_onboarding(self) -> None:
        if not hasattr(self, "_onboarding_card") or self._onboarding_card.isHidden():
            return
        has_url = bool(self._url_edit.text().strip())
        has_draft = bool(self._config.seed_urls)
        marks = (
            ("✓" if has_url else "○", _("粘贴网址")),
            ("✓" if has_draft else "○", _("确认采集方案")),
            ("✓" if self._trial_ok else "○", _("试跑少量页面")),
            ("✓" if self._trial_ok else "○", _("可以全量运行")),
        )
        self._onboarding_text.setText(
            _("<b>第一次创建任务</b><br>")
            + "　".join(f"{mark} {label}" for mark, label in marks)
        )

    def _dismiss_onboarding(self) -> None:
        self._onboarding_card.setVisible(False)
        from ...gui.settings import make_qsettings

        make_qsettings("OmniCrawler", "GUIWorkbench").setValue(self._ONBOARDING_KEY, True)

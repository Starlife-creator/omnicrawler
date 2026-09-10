"""TaskCanvas 的「AI 计划审核」域 —— 由 task_canvas.py 抽出的 Mixin（P1-3 第三批）。

PRD §5：审核动作与 AI 解耦。涵盖：
- ``_request_text``：意图区完整请求文本（URL + 一句话描述）
- ``_start_plan_review`` / ``_on_ai_plan_unavailable`` / ``_on_ai_plan_error``：后台生成与失败降级
- ``_on_ai_plan_ready`` / ``_accept_plan`` / ``_dismiss_plan``：审核卡片的展示、采纳与忽略

AI 未启用或出错一律静默降级，本地手动轨不受影响。
以 Mixin 形式保留 ``self`` 语义，宿主属性结构（五区 section 等）与调用点完全不变。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..i18n import _
from ..widgets.toast import ToastManager
from .task_canvas_components import PlanReviewWorker as _PlanReviewWorker
from .task_canvas_logic import SOURCE_KIND_SHORT as _SOURCE_KIND_SHORT

if TYPE_CHECKING:
    from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QWidget

    from .task_canvas_components import _Section

    # 类型检查期把宿主视作 QWidget（本域的 worker 会以 self 作为 parent）；
    # 运行期仍为 object，故不引入 Qt 基类、不改变 TaskCanvas 的 MRO。
    _Base = QWidget
else:
    _Base = object

LOGGER = logging.getLogger(__name__)


class AiPlanReviewMixin(_Base):
    """AI 计划审核卡片（PRD §5：审核动作与 AI 解耦）。"""

    # ---- 宿主契约（TaskCanvas 在 __init__ / 各 _build_* 中创建）----
    _project_root: str | None
    _ai_plan_enabled: bool
    _ai_plan_draft: Any | None
    _plan_worker: _PlanReviewWorker | None
    _plan_card: QFrame
    _plan_title: QLabel
    _plan_source_label: QLabel
    _plan_text: QLabel
    _url_edit: QLineEdit
    _desc_edit: QLineEdit
    _draft_section: _Section
    _fields_section: _Section

    if TYPE_CHECKING:
        # 由 TaskCanvas / 其他 Mixin 提供；仅类型检查期可见，运行期不存在，故不遮蔽宿主实现。
        def apply_draft(self, draft: Any) -> None: ...
        def _clear_dirty(self) -> None: ...
        def _collapse_section(self, section: _Section, collapsed: bool) -> None: ...
        def _refresh_recommendation(self) -> None: ...
        def _sync_ui_state(self) -> None: ...

    # ------------------------------------------------------------------
    #  P4：AI 计划审核卡片（PRD §5：审核动作与 AI 解耦）
    # ------------------------------------------------------------------
    def _request_text(self) -> str:
        """意图区完整请求文本（URL + 一句话描述），供 AI 计划解析。"""
        url = self._url_edit.text().strip()
        desc = self._desc_edit.text().strip()
        return (" ".join(part for part in (url, desc) if part)).strip()

    def _start_plan_review(self) -> None:
        """后台生成 AI 计划；请求为空、已禁用或已有 worker 在跑则跳过。"""
        request = self._request_text()
        if not self._ai_plan_enabled or not request or self._plan_worker is not None:
            return
        worker = _PlanReviewWorker(request, self, project_root=self._project_root)
        worker.result_ready.connect(self._on_ai_plan_ready)
        worker.ai_unavailable.connect(self._on_ai_plan_unavailable)
        worker.ai_error.connect(self._on_ai_plan_error)
        self._plan_worker = worker
        worker.start()

    def _on_ai_plan_ready(self, draft: Any) -> None:
        """AI 计划返回：展示审核卡片；仅接受当前 worker 的结果（restart 后旧结果丢弃）。"""
        if self.sender() is not self._plan_worker:
            return
        self._plan_worker = None
        if draft is None or not getattr(draft, "ai_enhanced", False):
            return
        self._ai_plan_draft = draft
        task = draft.task
        url = getattr(task, "url", "") or ""
        intent = str(getattr(task, "intent", "") or "")
        kind = _SOURCE_KIND_SHORT.get(str(getattr(task, "source_kind", "")), _("定向采集"))
        topics = "、".join(getattr(draft, "topics", ())) or _("未指定主题")
        lines = [
            f"<b>{_('入口')}</b>　{url or _('沿用当前网址')}",
            f"<b>{_('采集方式')}</b>　{kind}　<small>{_('意图')}：{intent}</small>",
            f"<b>{_('主题')}</b>　{topics}",
        ]
        for assumption in getattr(draft, "ai_assumptions", ())[:3]:
            field = assumption.get("field", "")
            value = assumption.get("value", "")
            reason = assumption.get("reason", "")
            lines.append(f"<small>· {_('假设')} {field}={value}（{reason}）</small>")
        for rec in getattr(draft, "ai_recommendations", ())[:3]:
            lines.append(f"<small>· {_('建议')} {rec}</small>")
        self._plan_title.setText(_("🤖 AI 计划已生成"))
        self._plan_source_label.setText(_("AI 生成 · 建议仅供参考"))
        self._plan_text.setText("<br>".join(lines))
        self._plan_card.setVisible(True)
        self._collapse_section(self._draft_section, False)
        ToastManager.instance().info(_("AI 计划已生成：可采纳或忽略"))

    def _on_ai_plan_unavailable(self, reason: str) -> None:
        """AI 未启用/隐私禁用：不打扰，画布继续走本地轨（审核动作与 AI 解耦）。"""
        if self.sender() is not self._plan_worker:
            return
        self._plan_worker = None
        LOGGER.debug("AI plan unavailable: %s", reason)

    def _on_ai_plan_error(self, message: str) -> None:
        """AI 计划生成失败：静默降级，本地草稿不受影响。"""
        if self.sender() is not self._plan_worker:
            return
        self._plan_worker = None
        LOGGER.debug("AI plan review failed: %s", message)

    def _accept_plan(self) -> None:
        """采纳 AI 计划：应用 AI 草稿（随后照常复核字段并试跑，不绕过任何护栏）。"""
        if self._ai_plan_draft is None:
            return
        draft = self._ai_plan_draft
        self._ai_plan_draft = None
        self._plan_card.setVisible(False)
        self.apply_draft(draft.task)
        self._clear_dirty()
        self._sync_ui_state()
        self._refresh_recommendation()
        self._collapse_section(self._fields_section, False)
        ToastManager.instance().success(_("已采纳 AI 计划，请复核字段后试跑"))

    def _dismiss_plan(self) -> None:
        """忽略 AI 计划：保留本地草稿，仅收起卡片。"""
        self._ai_plan_draft = None
        self._plan_card.setVisible(False)


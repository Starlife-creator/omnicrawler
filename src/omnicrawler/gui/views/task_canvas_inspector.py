"""任务工作台可选「实时提取预览（Inspector）」抽屉面板。

定位：作为既有建任务三路径（场景卡片 / AI 自然语言 / 图形表单）的**可选增强组件**，
默认收起，不打扰新手心智；展开后允许对任意样例 HTML 即时预览重复列表容器、
核验当前字段规则命中率，并支持一键去重追加推断字段（绝不覆盖用户已有字段）。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...extraction.intelligent_scraper import (
    _parse_dom,
    detect_repeating_patterns,
    infer_fields,
    verify_config,
)
from ..core.config_model import CrawlConfig, FieldDef
from ..core.config_serializer import to_yaml
from ..design_system import SPACING
from ..i18n import _


def _source_tag_label(source: str) -> str:
    return _(
        {
            "builtin": "内置",
            "plugin": "插件",
            "component": "组件",
        }.get(source, source)
    )


def slot_combo_items(slot_name: str, *, include_auto: bool = False) -> list[tuple[str, str]]:
    """从 ``CapabilitySlot`` 生成就地下拉选项 ``(value, label)``（内置/插件/组件平等呈现）。"""
    from ...core.capabilities import default_capability_slots

    items: list[tuple[str, str]] = []
    if include_auto:
        items.append(("", _("自动择优（推荐）")))
    slot = default_capability_slots().slot(slot_name)
    if slot is None:
        return items
    for provider in slot.providers():
        badge = _source_tag_label(provider.source)
        ready_suffix = "" if provider.is_available() else _(" · 未安装")
        label = f"{provider.display_name} [{badge}]{ready_suffix}"
        items.append((provider.provider_id, label))
    return items


class FieldPreviewInspectorPanel(QFrame):
    """按需展开的 DOM 结构与当前字段规则实时预览抽屉。"""

    candidates_accepted = Signal(str, list)  # (item_selector, list[FieldDef])

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("fieldPreviewInspector")
        self.setProperty("card", True)
        self.setAccessibleName(_("实时提取预览面板"))

        self._inferred_selector: str = ""
        self._inferred_fields: list[FieldDef] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"])
        layout.setSpacing(SPACING["sm"])

        header_row = QHBoxLayout()
        title = QLabel(_("🔬 实时提取预览（离线核验选择器与候选字段）"))
        title.setObjectName("sectionTitle")
        title.setAccessibleName(_("实时提取预览标题"))
        header_row.addWidget(title)
        header_row.addStretch()

        self.preview_btn = QPushButton(_("即时刷新预览"), self)
        self.preview_btn.setProperty("primary", True)
        self.preview_btn.setAccessibleName(_("即时刷新提取预览"))
        header_row.addWidget(self.preview_btn)

        self.apply_candidates_btn = QPushButton(_("＋ 追加推断字段"), self)
        self.apply_candidates_btn.setAccessibleName(_("将推断候选字段追加到字段表"))
        self.apply_candidates_btn.setToolTip(_("只去重追加新字段，绝不覆盖已有字段配置"))
        self.apply_candidates_btn.setEnabled(False)
        self.apply_candidates_btn.clicked.connect(self._emit_candidates)
        header_row.addWidget(self.apply_candidates_btn)
        layout.addLayout(header_row)

        self.html_input = QPlainTextEdit(self)
        self.html_input.setProperty("codeEditor", True)
        self.html_input.setAccessibleName(_("预览样例 HTML 输入"))
        self.html_input.setPlaceholderText(
            _("粘贴目标网页 HTML 片段，点击「即时刷新预览」即可离线查看列表容器识别与当前字段命中结果…")
        )
        self.html_input.setMaximumHeight(110)
        layout.addWidget(self.html_input)

        self.result_output = QPlainTextEdit(self)
        self.result_output.setReadOnly(True)
        self.result_output.setAccessibleName(_("实时提取预览结果"))
        self.result_output.setPlaceholderText(_("预览结果将在此处显示（含匹配记录数与各字段非空命中情况）。"))
        self.result_output.setMaximumHeight(140)
        layout.addWidget(self.result_output)

    def inspect_html(self, html: str, config: CrawlConfig) -> dict[str, Any]:
        """对给定 HTML 执行结构推断与当前规则试提，更新面板并返回结构化报告。"""
        import yaml

        if html != self.html_input.toPlainText():
            self.html_input.setPlainText(html)
        if not html.strip():
            self._inferred_selector = ""
            self._inferred_fields = []
            self.apply_candidates_btn.setEnabled(False)
            self.result_output.setPlainText(_("请先粘贴非空 HTML 内容后再执行预览。"))
            return {"items": 0, "inferred_fields": [], "verified": {}}

        parser_pref = str((config.passthrough.get("extract") or {}).get("parser") or "auto")
        nodes = _parse_dom(html, parser=parser_pref)
        patterns = detect_repeating_patterns(nodes)
        raw_candidates = infer_fields(patterns, nodes)
        best_selector = patterns[0].css_path if patterns else ""

        inferred_defs: list[FieldDef] = []
        for item in raw_candidates:
            name = str(item.get("name") or "").strip()
            sel = str(item.get("selector") or "").strip()
            if not name or not sel:
                continue
            attr = str(item.get("attribute") or "")
            pos = str(item.get("position") or "child")
            inferred_defs.append(
                FieldDef(
                    name=name,
                    selector=sel,
                    selector_type="css",
                    attribute=attr,
                    position=pos if pos in ("child", "element", "element_attr") else "child",
                )
            )

        self._inferred_selector = best_selector
        self._inferred_fields = inferred_defs
        self.apply_candidates_btn.setEnabled(bool(inferred_defs or best_selector))

        raw_cfg = yaml.safe_load(to_yaml(config)) or {}
        verified: dict[str, Any] = (
            verify_config(raw_cfg, html) if config.fields else {"items": 0, "fields": {}}
        )

        lines: list[str] = []
        if best_selector:
            lines.append(
                _("【结构识别】列表容器候选：{0}（共 {1} 项）").format(
                    best_selector, patterns[0].count
                )
            )
        else:
            lines.append(_("【结构识别】未检测到显著重复列表，按单页对象模式推断"))

        if inferred_defs:
            cand_summary = "、".join(f"{f.name}({f.selector})" for f in inferred_defs[:8])
            lines.append(_("【推断候选】{0}").format(cand_summary))

        if config.fields:
            raw_hits = verified.get("fields")
            field_hits = raw_hits if isinstance(raw_hits, dict) else {}
            hit_str = " · ".join(f"{k}: {v}" for k, v in field_hits.items()) or _("无命中")
            lines.append(
                _("【当前规则实测】匹配容器 {0} 条 ｜ 字段非空命中：{1}").format(
                    verified.get("items", 0), hit_str
                )
            )
        else:
            lines.append(_("【当前规则实测】字段表当前为空，可点击「＋ 追加推断字段」快速填入。"))

        self.result_output.setPlainText("\n".join(lines))
        return {
            "best_selector": best_selector,
            "inferred_fields": inferred_defs,
            "verified": verified,
        }

    def _emit_candidates(self) -> None:
        if self._inferred_selector or self._inferred_fields:
            self.candidates_accepted.emit(self._inferred_selector, list(self._inferred_fields))

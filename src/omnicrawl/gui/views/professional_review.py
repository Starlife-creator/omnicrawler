"""Evidence viewer: deep-dive into a single record from the results table.

从「结果与复核」页面的结果列表中点击某条记录后跳入，
展示该条记录的完整字段、原始证据和置信度信息。

Phase 2 落地：完整证据链展示（原始数据 + 字段表格 + 置信度 + 风险指示）。
"""

from __future__ import annotations

import json
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...review.review_workbench import ReviewField, ReviewItem
from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _


def _confidence_color(confidence: float) -> QColor:
    """置信度 → 颜色渐变：绿(高) → 黄(中) → 红(低)。"""
    if confidence >= 0.9:
        return QColor("#22c55e")
    elif confidence >= 0.7:
        return QColor("#eab308")
    elif confidence >= 0.5:
        return QColor("#f97316")
    else:
        return QColor("#ef4444")


def _risk_color(score: float) -> QColor:
    """风险评分 → 颜色渐变。"""
    if score <= 20:
        return QColor("#22c55e")
    elif score <= 50:
        return QColor("#eab308")
    elif score <= 80:
        return QColor("#f97316")
    else:
        return QColor("#ef4444")


def _origin_label(origin: str) -> str:
    """来源代码 → 中文标签。"""
    return {"raw": _("原始值"), "rule": _("规则"), "ai": "AI", "human": _("人工")}.get(origin, origin)


def _build_review_item(record: dict[str, Any]) -> ReviewItem:
    """从 JSONL 记录构建 ReviewItem。"""
    record_id = str(record.get("record_id", ""))
    source_url = str(record.get("source_url", record.get("url", "")))

    fields: list[ReviewField] = []
    field_values = record.get("field_values", record.get("fields", {}))
    if isinstance(field_values, list):
        for fv in field_values:
            if isinstance(fv, dict):
                fields.append(ReviewField(
                    name=str(fv.get("field_name", fv.get("name", "?"))),
                    value=fv.get("normalized_value", fv.get("value", "")),
                    origin=fv.get("extraction_method", fv.get("origin", "raw")),  # type: ignore[arg-type]
                    evidence=str(fv.get("evidence", ""))[:300],
                    confidence=float(fv.get("confidence", 1.0)),
                    page=fv.get("page_no", fv.get("page")),
                ))
    elif isinstance(field_values, dict):
        for name, fv in field_values.items():
            if isinstance(fv, dict):
                fields.append(ReviewField(
                    name=name,
                    value=fv.get("normalized_value", fv.get("value", "")),
                    origin=fv.get("extraction_method", fv.get("origin", "raw")),  # type: ignore[arg-type]
                    evidence=str(fv.get("evidence", ""))[:300],
                    confidence=float(fv.get("confidence", 1.0)),
                    page=fv.get("page_no", fv.get("page")),
                ))

    return ReviewItem(
        record_id=record_id,
        source_url=source_url,
        fields=fields,
        missing_required=tuple(record.get("missing_required", ())),
        rule_conflicts=int(record.get("rule_conflicts", 0)),
        ai_conflicts=int(record.get("ai_conflicts", 0)),
        structure_drift=float(record.get("structure_drift", 0.0)),
        ocr_quality=float(record.get("ocr_quality", 1.0)),
        duplicate=bool(record.get("duplicate", False)),
        pending_deletion=bool(record.get("pending_deletion", False)),
    )


class EvidenceView(QWidget):
    """证据查看器视图。

    从结果列表一键跳入，展示单条记录的完整证据链：
    - 顶部：记录信息栏 + 风险评分
    - 左栏：原始 JSON 证据
    - 右栏：字段详情表格（值 / 来源 / 证据 / 置信度）
    """

    # 返回结果列表信号
    back_to_results = pyqtSignal()  # type: ignore[has-type]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("证据查看器"))

        self._current_item: ReviewItem | None = None
        self._raw_record: dict[str, Any] | None = None

        self._setup_ui()
        self._apply_style()
        ThemeManager.instance().theme_changed.connect(self._apply_style)

    # ── UI 搭建 ────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 顶部：返回按钮 + 导出 + 标题
        top_bar = QHBoxLayout()
        self._back_btn = QPushButton(_("← 返回结果列表"))
        self._back_btn.clicked.connect(self._on_back)
        top_bar.addWidget(self._back_btn)

        self._export_md_btn = QPushButton(_("导出 Markdown"))
        self._export_md_btn.setToolTip(_("将当前记录的完整证据链导出为 Markdown 文件"))
        self._export_md_btn.clicked.connect(self._export_markdown)
        top_bar.addWidget(self._export_md_btn)
        top_bar.addStretch()

        self._record_title = QLabel("")
        self._record_title.setObjectName("homeTitle")
        top_bar.addWidget(self._record_title)
        top_bar.addStretch()
        root.addLayout(top_bar)

        # 记录信息栏
        info_card = QFrame()
        info_card.setProperty("card", True)
        info_layout = QHBoxLayout(info_card)
        info_layout.setContentsMargins(16, 10, 16, 10)
        info_layout.setSpacing(24)

        self._info_record_id = QLabel("")
        self._info_record_id.setObjectName("mutedLabel")
        info_layout.addWidget(self._info_record_id)

        self._info_source = QLabel("")
        self._info_source.setObjectName("mutedLabel")
        self._info_source.setWordWrap(True)
        info_layout.addWidget(self._info_source, 1)

        self._info_fields_count = QLabel("")
        self._info_fields_count.setObjectName("mutedLabel")
        info_layout.addWidget(self._info_fields_count)

        # 风险评分徽章
        self._risk_badge = QLabel("")
        self._risk_badge.setObjectName("riskBadge")
        self._risk_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._risk_badge.setFixedSize(80, 36)
        info_layout.addWidget(self._risk_badge)

        # 风险详情
        self._risk_details = QLabel("")
        self._risk_details.setObjectName("mutedLabel")
        info_layout.addWidget(self._risk_details)

        root.addWidget(info_card)

        # 主体：QSplitter 左证据 + 右表格
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        # ── 左栏：原始证据 ──
        left_panel = QFrame()
        left_panel.setProperty("card", True)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)

        left_header = QLabel(_("原始证据"))
        left_header.setObjectName("sectionSubtitle")
        left_layout.addWidget(left_header)

        self._evidence_text = QTextEdit()
        self._evidence_text.setReadOnly(True)
        self._evidence_text.setPlaceholderText(_("选择一条记录以查看原始证据数据"))
        left_layout.addWidget(self._evidence_text, 1)

        splitter.addWidget(left_panel)

        # ── 右栏：字段详情表 ──
        right_panel = QFrame()
        right_panel.setProperty("card", True)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)

        right_header = QLabel(_("字段详情"))
        right_header.setObjectName("sectionSubtitle")
        right_layout.addWidget(right_header)

        self._field_table = QTableWidget()
        self._field_table.setColumnCount(5)
        self._field_table.setHorizontalHeaderLabels([_("字段"), _("值"), _("来源"), _("证据"), _("置信度")])
        h_header = self._field_table.horizontalHeader()
        assert h_header is not None
        h_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._field_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._field_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._field_table.setAlternatingRowColors(True)
        v_header = self._field_table.verticalHeader()
        assert v_header is not None
        v_header.setVisible(False)
        right_layout.addWidget(self._field_table, 1)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

    # ── 样式 ───────────────────────────────────────────────────
    def _apply_style(self, *_args: Any) -> None:
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QLabel#homeTitle {{
                font-size: {FONT_SIZE["heading"]}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#sectionSubtitle {{
                font-size: {FONT_SIZE["body"]}px;
                color: {t.text};
                font-weight: 600;
            }}
            QLabel#mutedLabel {{
                font-size: {FONT_SIZE["small"]}px;
                color: {t.muted};
            }}
            QLabel#riskBadge {{
                font-size: {FONT_SIZE["subtitle"]}px;
                font-weight: 700;
                color: white;
                border-radius: {RADIUS["lg"]}px;
                padding: 4px;
            }}
            QTextEdit {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 8px;
                background: {t.surface};
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
            QTableWidget {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                gridline-color: {t.border};
                background: {t.surface};
                font-size: {FONT_SIZE["small"]}px;
            }}
            QTableWidget::item {{
                padding: 4px 8px;
            }}
            QHeaderView::section {{
                background: {t.canvas};
                padding: 6px 8px;
                border: none;
                border-bottom: 2px solid {t.border};
                font-weight: 600;
                font-size: {FONT_SIZE["small"]}px;
                color: {t.text};
            }}
        """)

    # ── 公开接口 ───────────────────────────────────────────────
    @pyqtSlot(object)
    def show_record(self, record: dict[str, Any]) -> None:
        """从 JSONL 记录字典加载并展示完整证据链。"""
        self._raw_record = record
        self._current_item = _build_review_item(record)
        self._render()

    def show_item(self, item: ReviewItem) -> None:
        """从 ReviewItem 加载并展示。"""
        self._current_item = item
        self._raw_record = None
        self._render()

    def clear(self) -> None:
        """清空视图。"""
        self._current_item = None
        self._raw_record = None
        self._record_title.setText("")
        self._info_record_id.setText("")
        self._info_source.setText("")
        self._info_fields_count.setText("")
        self._risk_badge.setText("")
        self._risk_badge.setStyleSheet("")
        self._risk_details.setText("")
        self._evidence_text.clear()
        self._evidence_text.setPlaceholderText(_("选择一条记录以查看原始证据数据"))
        self._field_table.setRowCount(0)

    # ── 渲染 ───────────────────────────────────────────────────
    def _render(self) -> None:
        item = self._current_item
        if item is None:
            self.clear()
            return

        # 标题
        self._record_title.setText(_(f"记录: {item.record_id[:40]}{'...' if len(item.record_id) > 40 else ''}"))

        # 信息栏
        self._info_record_id.setText(f"ID: {item.record_id[:24]}...")
        self._info_source.setText(_(f"来源: {item.source_url[:60]}{'...' if len(item.source_url) > 60 else ''}"))
        self._info_source.setToolTip(item.source_url)
        self._info_fields_count.setText(_(f"字段: {len(item.fields)} 个"))

        # 风险评分
        risk = item.risk_score
        risk_color = _risk_color(risk)
        self._risk_badge.setText(f"{risk:.0f}")
        self._risk_badge.setStyleSheet(
            f"background-color: {risk_color.name()}; color: white; " +

            f"font-weight: 700; border-radius: {RADIUS['lg']}px; padding: 4px;"
        )

        risks_parts: list[str] = []
        if item.missing_required:
            risks_parts.append(_(f"缺{len(item.missing_required)}项必填"))
        if item.rule_conflicts:
            risks_parts.append(_(f"{item.rule_conflicts}规则冲突"))
        if item.ai_conflicts:
            risks_parts.append(_(f"{item.ai_conflicts}AI冲突"))
        if item.structure_drift > 0.1:
            risks_parts.append(_("结构漂移"))
        if item.ocr_quality < 0.8:
            risks_parts.append(_("OCR质量低"))
        if item.duplicate:
            risks_parts.append(_("重复"))
        self._risk_details.setText(", ".join(risks_parts) if risks_parts else _("无风险"))

        # 原始证据
        if self._raw_record:
            self._evidence_text.setPlainText(
                json.dumps(self._raw_record, ensure_ascii=False, indent=2, default=str)
            )
        else:
            self._evidence_text.setPlainText(
                json.dumps({
                    "record_id": item.record_id,
                    "source_url": item.source_url,
                    "risk_score": risk,
                    "missing_required": item.missing_required,
                    "rule_conflicts": item.rule_conflicts,
                    "structure_drift": item.structure_drift,
                    "ocr_quality": item.ocr_quality,
                }, ensure_ascii=False, indent=2)
            )

        # 字段表格
        self._field_table.setRowCount(len(item.fields))
        for row, field in enumerate(item.fields):
            # 字段名
            name_item = QTableWidgetItem(field.name)
            name_item.setToolTip(field.name)
            self._field_table.setItem(row, 0, name_item)

            # 值
            value_text = str(field.value) if field.value is not None else "—"
            value_item = QTableWidgetItem(value_text)
            value_item.setToolTip(value_text)
            self._field_table.setItem(row, 1, value_item)

            # 来源
            origin_item = QTableWidgetItem(_origin_label(field.origin))
            self._field_table.setItem(row, 2, origin_item)

            # 证据（截断）
            evidence_text = field.evidence[:200] + "..." if len(field.evidence) > 200 else field.evidence
            evidence_item = QTableWidgetItem(evidence_text or "—")
            if field.evidence:
                evidence_item.setToolTip(field.evidence)
            self._field_table.setItem(row, 3, evidence_item)

            # 置信度（带颜色）
            conf_text = f"{field.confidence:.0%}" if field.confidence <= 1 else f"{field.confidence:.2f}"
            conf_item = QTableWidgetItem(conf_text)
            conf_color = _confidence_color(field.confidence)
            conf_item.setForeground(conf_color)
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._field_table.setItem(row, 4, conf_item)

    # ── 导出 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _export_markdown(self) -> None:
        """导出当前记录证据为 Markdown 文件。"""
        if self._raw_record is None:
            QMessageBox.information(self, _("提示"), _("当前没有可导出的记录。"))
            return

        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self, _("导出 Markdown"), f"record_{self._raw_record.get('record_id', 'evidence')}.md",
            _("Markdown 文件 (*.md)"),
        )
        if not output_path:
            return

        try:
            from pathlib import Path

            from omnicrawl.export.markdown_exporter import MarkdownExporter

            MarkdownExporter.export_single_record(
                self._raw_record,
                output_path=Path(output_path),
                style="card",
            )
            QMessageBox.information(self, _("导出成功"), _(f"已导出到: {output_path}"))
        except Exception as exc:
            QMessageBox.critical(self, _("导出失败"), str(exc))

    # ── 返回 ───────────────────────────────────────────────────
    @pyqtSlot()
    def _on_back(self) -> None:
        self.back_to_results.emit()


# 别名，保持向后兼容
ProfessionalReviewView = EvidenceView

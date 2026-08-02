"""Professional review desk: source evidence and risk-ranked fields side by side.

当前为功能开发中状态，使用 EmptyState 组件展示统一占位界面。
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from ...review.review_workbench import ReviewItem
from ..i18n import _
from ..widgets.empty_state import EmptyState


class ProfessionalReviewView(QWidget):
    """专业复核台视图。

    当前阶段：功能开发中。展示 EmptyState 占位界面，
    包含功能说明和"返回配置向导"操作引导。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("专业复核台"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)

        self._empty = EmptyState(
            icon="✓",
            title=_("专业复核台 · 功能开发中"),
            description=_(
                "此处将提供按风险优先排序的字段复核界面：\n"
                "• 左侧显示原网页/PDF 证据与命中区域\n"
                "• 右侧显示待复核字段表格（值/来源/证据/置信度）\n"
                "• 支持批量确认、保留原值、生成规则建议\n"
                "• 风险过滤：必填缺失、规则冲突、结构漂移、OCR 低质量\n\n"
                "当前请使用「结果与复核」页面查看已采集数据。"
            ),
            parent=self,
        )
        layout.addWidget(self._empty)

    def show_item(self, item: ReviewItem) -> None:
        """预留：显示复核项。"""
        pass

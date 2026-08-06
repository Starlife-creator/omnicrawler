"""Developer-mode IR, plan, event, replay and plugin inspection surface.

当前为功能开发中状态，使用 EmptyState 组件展示统一占位界面。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ..i18n import _
from ..widgets.empty_state import EmptyState


class DeveloperInspector(QWidget):
    """开发者检查器视图。

    当前阶段：功能开发中。展示 EmptyState 占位界面，
    包含功能说明和操作引导。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("开发者检查器"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)

        self._empty = EmptyState(
            icon="</>",
            title=_("开发者检查器 · 功能开发中"),
            description=_(
                _("此处将提供开发者调试界面：\n") +

                _("• Task IR（任务中间表示）查看与编辑\n") +

                _("• 执行计划与权限审计\n") +

                _("• 网络/API 证据（经 Egress 脱敏）\n") +

                _("• 阶段事件与性能时间线\n") +

                _("• 离线回放（确定性无网络重放）\n") +

                _("• 插件权限审计面板\n\n") +

                _("当前请使用 YAML 编辑器查看配置详情。")
            ),
            parent=self,
        )
        layout.addWidget(self._empty)

    def update_config(self, config) -> None:
        """预留：更新配置。"""
        pass

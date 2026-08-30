"""示例 UI 插件：注册主题 / 菜单动作 / 侧栏面板 / 状态栏小部件。

启用方式（本地插件）：
  1. 复制本文件到项目根 plugins/ 目录（默认加载路径）；
  2. 创建并签名：python tools/identity.py create <你的用户名>
     python tools/sign_plugin.py local-sign plugins --username <你的用户名> --file plugin.py
     （local-sign = 创作者签名 + 自动加入本地信任列表）
  3. 启动 GUI 后主题出现在 设置 → 主题 菜单，动作出现在 插件 菜单。

权限：ui:theme / ui:action / ui:panel / ui:status。
本示例属于契约 1 原生 UI，只适用于本地高信任、in-process 场景；当前公开市场只接受契约 2，
不能把本示例直接作为市场 GUI 插件投稿。
"""

from __future__ import annotations

PLUGIN_METADATA = {
    "name": "example-ui",
    "version": "1.0.0",
    "description": "示例 UI 插件：主题、动作、面板、状态栏",
    "permissions": ["ui:theme", "ui:action", "ui:panel", "ui:status"],
}


def _make_panel(mw):
    """侧栏面板：一个简单的欢迎面板。"""
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    widget = QWidget(mw)
    layout = QVBoxLayout(widget)
    layout.addWidget(QLabel("你好，我是示例 UI 插件面板。", widget))
    return widget


def _make_status_widget():
    """状态栏常驻小部件。"""
    from PySide6.QtWidgets import QLabel

    return QLabel("  UI 插件已加载  ")


def register(registry):
    registry.register_theme(
        "example_forest",
        "森林绿",
        tokens={
            "canvas": "#101C16",
            "surface": "#1A2B21",
            "elevated": "#22382B",
            "nav": "#15231B",
            "text": "#E8F2EA",
            "muted": "#9DB8A5",
            "border": "#2E4A3A",
            "border_strong": "#4A7560",
            "primary": "#6FBF8F",
            "primary_hover": "#8AD3A8",
            "primary_active": "#55A877",
            "selection": "#24402F",
            "success": "#6FBF8F",
            "success_bg": "#1C3325",
            "warning": "#E3B15C",
            "warning_bg": "#33291A",
            "danger": "#EF7777",
            "danger_bg": "#33201F",
            "info": "#6FBF8F",
            "info_bg": "#1A2E26",
        },
    )

    def _greet(mw):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(mw, "示例插件", "你好，OmniCrawler！")

    registry.register_ui_action("example.greet", "示例：问候", _greet)

    registry.register_ui_panel("example.welcome", "示例面板", _make_panel)
    registry.register_status_widget(_make_status_widget)

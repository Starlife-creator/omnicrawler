"""把「切换已注册插件主题」接到 `theme.apply` 能力（2026-11-27）。

## 为什么单独一个模块

这段逻辑本来可以写成 `MainWindow` 的一个方法 —— 但 `check_main_window_size`
守着「MainWindow 只降不升」（文件行数 + 类方法数），而这段逻辑**和主窗口的
职责无关**：它只是「把 design_system 里已注册的主题切过去」。

放进独立模块既满足门禁，也把它放到了正确的位置 ——
`main.py` 只是调用一行。

## 安全边界

只认 `design_system` 里**已注册**的插件主题：插件传任意字符串都不会生效，
也不能借这条能力注入任意色值（`plugin_theme_tokens` 返回 None 即拒绝）。
"""
from __future__ import annotations

import weakref
from typing import Any


def _apply_plugin_theme(window: Any, theme_id: str) -> bool:
    """切到**已注册**的插件主题；未注册返回 False（不静默成功）。"""
    from ..gui.design_system import plugin_theme_tokens

    if plugin_theme_tokens(theme_id) is None:
        return False
    manager = getattr(window, "_theme_manager", None)
    set_theme = getattr(manager, "set_theme", None)
    if not callable(set_theme):
        return False
    set_theme(str(theme_id).strip().lower())
    return True


def install_theme_apply_handler(window: Any) -> None:
    """把主窗口的主题管理器注册成 `theme.apply` 的宿主端实现。

    用弱引用持有窗口：注册表是模块级的，强引用会让窗口无法回收
    （`theme.apply` 的生命周期比任何一个窗口都长，但它只该在窗口存在时生效）。
    """
    from ..plugins.plugin_broker_host import register_theme_apply_handler

    ref = weakref.ref(window)

    def _handler(theme_id: str) -> bool:
        target = ref()
        if target is None:
            return False
        return _apply_plugin_theme(target, theme_id)

    register_theme_apply_handler(_handler)

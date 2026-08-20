from __future__ import annotations

import importlib.util

import pytest

# gui.main defines classes inheriting PyQt6 widgets at module level
# (MainWindow, etc.) outside the _cli_mode() guard,
# so the module cannot be imported without PyQt6 installed.
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="gui.main requires PyQt6 at module level",
)


def test_headless_module_has_module_level_translation_function() -> None:
    """S1.1.4：_() 提到模块顶层，headless 模式（不加载 PyQt6）也能引用。"""
    import omnicrawler.gui.main as gui_main

    assert callable(gui_main._)
    assert gui_main._("测试") == "测试"  # 无 .mo 文件时返回原文

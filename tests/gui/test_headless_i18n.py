from __future__ import annotations

import omnicrawl.gui.main as gui_main


def test_headless_module_has_module_level_translation_function() -> None:
    """S1.1.4：_() 提到模块顶层，headless 模式（不加载 PyQt6）也能引用。"""
    assert callable(gui_main._)
    assert gui_main._("测试") == "测试"  # 无 .mo 文件时返回原文

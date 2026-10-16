from __future__ import annotations

import importlib.util

import pytest

# gui.main imports PySide6 unconditionally at module level
# (MainWindow, etc. subclass QMainWindow), so the module cannot be
# imported without PySide6 installed.
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="gui.main requires PySide6 at module level",
)


def test_module_level_translation_function() -> None:
    """S1.1.4：_() 提到模块顶层，无需实例化任何 GUI 组件即可引用。"""
    import omnicrawler.gui.main as gui_main

    assert callable(gui_main._)
    assert gui_main._("测试") == "测试"  # 无 .mo 文件时返回原文

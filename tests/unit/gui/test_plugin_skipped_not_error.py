"""`level == "skipped"` 的插件条目不是错误，不得进入 GUI 错误列表。

为什么要有这个文件（2026-11-25 实测）：

默认插件搜索路径是 `["plugins/", "plugins_installed/"]`。全新用户这两个目录
**本来就不存在** —— 那是零配置的正常状态，不是故障。

但 `_install_plugin_ui` 只放过「未在当前项目启用」这一种 skipped：

    if item.get("level") != "skipped" or "未在当前项目启用" not in reason:
        load_errors.append(f"{path}: {reason}")

于是每次启动都报：

    GUI plugin reload completed with errors: plugins_installed/: skipped: 路径不存在

`notify=True` 时还会弹 Toast「有 1 个插件未能加载」。
用户看到的是「我什么都没装，怎么就坏了」。

本文件钉住：skipped 一律不进 `_plugin_ui_errors`，但真失败照旧进。
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_no_missing_path_error_at_startup(qapp) -> None:
    """零配置（插件目录都不存在）时，`_plugin_ui_errors` 必须不含「路径不存在」。"""
    from omnicrawler.gui.main import MainWindow

    window: Any = MainWindow()
    try:
        errors = [str(e) for e in getattr(window, "_plugin_ui_errors", [])]
        offending = [e for e in errors if "路径不存在" in e]
        assert not offending, (
            "不存在的默认插件目录被当成了错误 —— 零配置用户会看到「插件未能加载」："
            + repr(offending)
        )
    finally:
        window.deleteLater()


def test_skipped_level_items_are_filtered() -> None:
    """直接验证过滤函数：skipped 全丢，无 level 的真失败保留。"""
    from omnicrawler.gui.plugin_host import plugin_error_lines

    items = [
        {"path": "plugins_installed/", "error": "skipped: 路径不存在", "level": "skipped"},
        {"path": "plugins/", "error": "skipped: 路径不存在", "level": "skipped"},
        {"path": "plugins/", "error": "skipped: 市场插件 x 未在当前项目启用", "level": "skipped"},
        {"path": "bad/plugin.py", "error": "SignatureError: 签名无效"},
    ]
    kept = plugin_error_lines(items)
    assert len(kept) == 1, kept
    assert "bad/plugin.py" in kept[0]
    assert not any("路径不存在" in line for line in kept)

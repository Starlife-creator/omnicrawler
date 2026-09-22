"""`tests/conftest.py::_reap_leaked_gui_widgets` 的契约守卫（防 3.5× 性能回退）。

## 为什么需要守卫

GUI 用例会在进程里留下**从未销毁**的顶层控件。实测（2026-09-22）：仅跑 2 个 GUI 文件后
进程里就有 1016 个存活控件；`ThemeManager.apply()` 每次都会 `setStyleSheet`/`setPalette`/
改 app 字体，Qt 因此对所有存活控件重算样式（泄漏窗口还连着 `theme_changed` 单例信号），
于是**后跑的** GUI 用例被拖死：

| 用例 | 单独跑 | 整套里（回收前） |
| --- | --- | --- |
| `test_all_theme_variants_pass_strict_hex_guard` | 0.05s | 51.8s |
| `test_monitor_icon_changes_with_the_theme` | < 1s | 48.9s |

加回收 fixture 后本地全套 806.9s → 225.7s（通过/跳过数逐字不变）。

## 本文件怎么守住它

本文件刻意写成**两个必须按序执行**的用例：
① 造一个"泄漏"（模块级引用持有顶层控件，模拟真实泄漏）；
② 断言它**没有活到下一个用例**。
一旦回收 fixture 被删掉或写坏，① 造的控件就会存活到 ②，② 必须转红。

反向断言（已实测）：把 `_reap_leaked_gui_widgets` 的回收体注释掉 ⇒ ② 失败。
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="GUI 测试需要 PySide6"
)

#: 模块级引用 ⇒ 控件不会被 GC，模拟"测试没销毁窗口"的真实泄漏
_LEAKED: list[object] = []

_PROBE = "c3-widget-reap-probe"


def test_a_leaking_widget_is_created() -> None:
    """① 造一个泄漏：顶层控件只被模块级列表引用，没有任何地方会销毁它。"""
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    before = [w for w in app.topLevelWidgets() if w.objectName() == _PROBE]

    probe = QLabel("probe")
    probe.setObjectName(_PROBE)
    probe.show()
    _LEAKED.append(probe)

    after = [w for w in app.topLevelWidgets() if w.objectName() == _PROBE]
    assert not before, "上一个用例的探针没被回收，② 的断言会失去意义"
    assert len(after) == 1, f"探针没进顶层控件表（{len(after)} 个）⇒ 本守卫会变成空对空的假通过"


def test_b_leak_does_not_survive_into_the_next_test() -> None:
    """② 上一个用例留下的顶层控件必须已被回收。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert app is not None, "QApplication 应已由 ① 建立"

    survivors = [w for w in app.topLevelWidgets() if w.objectName() == _PROBE]
    assert not survivors, (
        f"上一个用例泄漏的顶层控件还活着（{len(survivors)} 个）⇒ "
        "`tests/conftest.py::_reap_leaked_gui_widgets` 失效了。"
        "它一旦失效，整套里后跑的 GUI 用例会从 <1s 变成 30~50s（实测全套 3.5× 回退）。"
    )

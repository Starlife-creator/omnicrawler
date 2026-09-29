"""回收契约：委托 / 快捷方式管理器**不得强引用主窗口**。

为什么要有这条契约：主窗口持有全部委托（作为属性），若委托又强引用主窗口，
就形成"窗口 → 委托 → 窗口"的强引用环。Qt 控件（C++ 侧）在环里不会及时回收
⇒ 离屏用例里控件逐条累积，整套耗时被拖到几倍（Windows ≈323s / Linux ≈148s）。

判据：把主窗口的最后一条外部引用删掉、跑一次 gc 之后，弱引用必须**已死**。
反过来（先装回旧实现）这条用例必须变红——否则它只是"绿着好看"。
"""

from __future__ import annotations

import gc
import os
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from omnicrawler.gui.shortcuts import GlobalShortcutManager


@pytest.fixture(scope="module")
def app() -> QApplication:
    """QApplication（离屏）——没有它构造 QMainWindow 会让解释器直接崩（实测 exit 127）。"""
    return QApplication.instance() or QApplication([])


class _FakeMainWindow:
    """可被弱引用的最小替身（真实 MainWindow 的构造代价太大，不是本契约的被测点）。"""

    def close(self) -> None:  # GlobalShortcutManager 可能用到
        pass


@pytest.mark.xfail(
    strict=True,
    reason=(
        "已知未闭环（2026-09-29 度量完成、修复暂缓）：委托仍强引用主窗口。"
        "实测改 weakref 会打破两条合法用法 —— ① 测试桩 SimpleNamespace 不可弱引用；"
        "② 窗口先销毁、委托仍被使用（关停路径）。修复须连带处理这两处后再开；"
        "此前同类尝试即因这些问题被撤销。本用例是修复后的验收判据。"
    ),
)
def test_delegate_must_not_keep_main_window_alive() -> None:
    from omnicrawler.gui.delegates._base import _BaseDelegate

    window = _FakeMainWindow()
    alive = weakref.ref(window)
    delegate = _BaseDelegate(window)  # type: ignore[arg-type]
    assert delegate._mw is window, "委托应能正常访问主窗口"

    del window
    gc.collect()
    assert alive() is None, (
        "委托仍强引用主窗口 ⇒ 窗口销毁后无法回收（GUI 控件泄漏的根因）。"
        "契约：委托/管理器对主窗口只持有 weakref。"
    )


@pytest.mark.xfail(strict=True, reason="同上：GlobalShortcutManager 仍强引用主窗口（未闭环债）")
def test_shortcut_manager_stores_only_a_weakref(app: QApplication) -> None:
    """快捷键管理器只准存 weakref。

    ★ 这里用**结构性断言**而不是"删掉窗口看它死没死"：管理器是窗口的 QObject 子对象，
    Qt 的父子链本身就会钉住窗口（那是正常的 Qt 所有权，不是泄漏）。真正要防的是
    Python 侧的"窗口 → 管理器 → 窗口"强引用环。
    """
    from PySide6.QtWidgets import QMainWindow

    window = QMainWindow()
    manager = GlobalShortcutManager(window)

    stored = manager.__dict__["_main_window"]
    assert isinstance(stored, weakref.ref), (
        "应只存 weakref；存强引用会与窗口构成环 ⇒ 回收不掉"
    )
    assert stored() is window, "窗口存活时仍应正确解析"
    manager.deleteLater()
    window.deleteLater()

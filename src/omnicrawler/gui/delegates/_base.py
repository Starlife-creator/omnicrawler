"""Base class for GUI delegates (explicit ``self._mw`` contract).

FINAL 长期债 #1 Phase B：显式耦合契约——委托对主窗口的一切访问必须
写作 self._mw.X。历史上的 __getattr__ 全量转发已删除：它曾让
文件级拆分退化为对象级耦合（委托可翻改主窗口任意私有状态而不被
察觉）。如今任何隐式转发尝试都会在开发期直接 AttributeError。

所有权规则：``__init__`` 内赋值 / 类级注解 / 本文件定义的方法与属性
属于委托自身；其余状态一律归 MainWindow 所有。
"""
from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, cast

from ..i18n import _

if TYPE_CHECKING:
    from ..main import MainWindow


class _BaseDelegate:
    """Base class for GUI delegates（显式 _mw 访问，见模块 docstring）。"""

    def __init__(self, mw: MainWindow) -> None:
        self._mw = mw

    @property
    def _mw(self) -> MainWindow:
        """按需还原主窗口。

        ★ 只持 weakref（回收契约，见 tests/unit/gui/test_widget_reaping_contract.py）：
        主窗口持有全部委托，委托若再强引用主窗口就成环 ⇒ Qt 控件回收不掉，
        离屏用例逐条累积把整套拖慢数倍。
        """
        window = self.__dict__["_mw"]()
        if window is None:
            raise RuntimeError(_("主窗口已销毁，委托不再可用"))
        return cast("MainWindow", window)

    @_mw.setter
    def _mw(self, value: MainWindow) -> None:
        try:
            self.__dict__["_mw"] = weakref.ref(value)
        except TypeError:
            # 不可弱引用的对象（测试桩等）⇒ 退回强引用：显式声明这条兼容边界的代价，
            # 但不让"桩对象"把调用方拦在门外（真实 MainWindow 是 QObject，可弱引用）。
            self.__dict__["_mw"] = lambda: value

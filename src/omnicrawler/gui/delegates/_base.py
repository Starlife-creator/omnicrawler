"""Base class for GUI delegates.

FINAL 长期债 #1 Phase B：**显式耦合契约**——委托对主窗口的一切访问必须
写作 ``self._mw.X``。历史上的 ``__getattr__`` 全量转发已删除：它曾让
"文件级拆分"退化为"对象级耦合"（委托可翻改主窗口任意私有状态而不被
察觉）。如今任何隐式转发尝试都会在开发期直接 AttributeError。

所有权规则：``__init__`` 内赋值 / 类级注解 / 本文件定义的方法与属性
属于委托自身；其余状态一律归 MainWindow 所有。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..main import MainWindow


class _BaseDelegate:
    """Base class for GUI delegates（显式 _mw 访问，见模块 docstring）。"""

    def __init__(self, mw: MainWindow) -> None:
        self._mw = mw

    @property
    def _mw(self) -> MainWindow:
        return self.__dict__["_mw"]

    @_mw.setter
    def _mw(self, value: MainWindow) -> None:
        self.__dict__["_mw"] = value

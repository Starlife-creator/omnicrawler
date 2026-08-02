"""Base class for GUI delegates.

Uses ``__getattr__`` to transparently forward any attribute not found
on the delegate to the main window. This allows method bodies copied
from MainWindow to work without modifying every ``self.`` reference.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..main import MainWindow


class _BaseDelegate:
    """Base class for GUI delegates.

    Uses ``__getattr__`` to transparently forward any attribute not found
    on the delegate to the main window. This allows method bodies copied
    from MainWindow to work without modifying every ``self.`` reference.
    """

    def __init__(self, mw: MainWindow) -> None:
        self._mw = mw

    def __getattr__(self, name: str) -> Any:
        # Only called when normal attribute lookup fails.
        # Forward to the main window.
        return getattr(self._mw, name)

    @property
    def _mw(self) -> MainWindow:
        return self.__dict__["_mw"]

    @_mw.setter
    def _mw(self, value: MainWindow) -> None:
        self.__dict__["_mw"] = value

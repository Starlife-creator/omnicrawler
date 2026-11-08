"""GUI delegate classes extracted from main.py.

Each delegate handles a specific functional domain of the MainWindow.
Delegates hold a **weak** reference to the main window (``_BaseDelegate``) and
reach it explicitly as ``self._mw.X``. The old ``__getattr__`` transparent
forwarding was removed in FINAL 长期债 #1 Phase B — an explicit ``self._mw.``
prefix is what makes a delegate's coupling to MainWindow greppable.

Only loaded when PySide6 is available.
"""
from ._base import _BaseDelegate as _BaseDelegate
from .central_area import CentralAreaBuilder as CentralAreaBuilder
from .config_manager import ConfigManager as ConfigManager
from .env_checker import EnvironmentChecker as EnvironmentChecker
from .error_dialog import ErrorDialogHelper as ErrorDialogHelper
from .help_dialog import HelpDialogManager as HelpDialogManager
from .login_session import LoginSessionDelegate as LoginSessionDelegate
from .menu import MenuBuilder as MenuBuilder
from .run_controller import RunController as RunController
from .theme import ThemeManager as ThemeManager
from .toolbar import ToolbarManager as ToolbarManager

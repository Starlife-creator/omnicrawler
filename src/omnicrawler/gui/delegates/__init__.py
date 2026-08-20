"""GUI delegate classes extracted from main.py.

Each delegate handles a specific functional domain of the MainWindow.
Delegates use ``__getattr__`` to transparently forward unknown attribute
access to the main window, so method bodies copied from MainWindow work
without modifying every ``self.`` reference.

Only loaded when PySide6 is available.
"""
from ._base import _BaseDelegate as _BaseDelegate
from .config_manager import ConfigManager as ConfigManager
from .env_checker import EnvironmentChecker as EnvironmentChecker
from .error_dialog import ErrorDialogHelper as ErrorDialogHelper
from .help_dialog import HelpDialogManager as HelpDialogManager
from .menu import MenuBuilder as MenuBuilder
from .run_controller import RunController as RunController
from .theme import ThemeManager as ThemeManager
from .toolbar import ToolbarManager as ToolbarManager

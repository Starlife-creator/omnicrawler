import importlib.util
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


def test_session_cleanup_stops_owner_and_deletes_cpp_widgets():
    assert QApplication.instance() is not None
    spec = importlib.util.spec_from_file_location("qt_test_cleanup", Path(__file__).parents[2] / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Owner(QWidget):
        def __init__(self):
            super().__init__()
            self.timer = QTimer(self)
            self.timer.start(30000)
            self.stopped = False

        def shutdown(self):
            self.timer.stop()
            self.stopped = True

    owner = Owner()
    module._dispose_qt_test_widgets(SimpleNamespace(topLevelWidgets=lambda: [owner]))
    assert owner.stopped
    assert not isValid(owner)

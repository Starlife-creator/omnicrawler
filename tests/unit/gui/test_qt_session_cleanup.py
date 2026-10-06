import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def test_per_test_cleanup_preserves_existing_widget_and_deletes_new_owner():
    spec = importlib.util.spec_from_file_location("qt_test_cleanup", Path(__file__).parents[2] / "conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    preserved = QWidget()
    owner = None
    cleanup = module._owned_qt_test_widgets.__wrapped__(None, None)
    try:
        next(cleanup)
        owner = QWidget()
        timer = QTimer(owner)
        timer.start(30000)
        with pytest.raises(StopIteration):
            next(cleanup)
        assert not isValid(owner), "Per-test widgets must be deleted before the next theme change"
        assert isValid(preserved), "Module-owned widgets must remain alive"
    finally:
        if owner is not None and isValid(owner):
            owner.deleteLater()
        preserved.deleteLater()

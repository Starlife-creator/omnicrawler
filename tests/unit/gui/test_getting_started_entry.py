from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.core.config_serializer import load_yaml, to_yaml
from omnicrawler.gui.home import HomePage
from omnicrawler.services.getting_started import create_starter


def test_home_starter_button_and_gui_config_round_trip(tmp_path):
    app = QApplication.instance() or QApplication([])
    home = HomePage(project_root=str(tmp_path))
    signal = QSignalSpy(home.create_starter)
    QTest.mouseClick(home.starter_button, Qt.MouseButton.LeftButton)
    assert signal.count() == 1
    source = create_starter(tmp_path)
    restored = to_yaml(load_yaml(source))
    assert "$.items[*]" in restored and "in_stock" in restored
    assert "kind: file" in restored
    home.shutdown()
    home.close()
    app.processEvents()

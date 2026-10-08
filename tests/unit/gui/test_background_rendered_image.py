"""PNG-only host rendering works across Qt format-argument signatures."""
import io
from types import SimpleNamespace

import pytest

QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
Image = pytest.importorskip("PIL.Image")

BackgroundController = pytest.importorskip("omnicrawler.gui.background_host").BackgroundController


def image_bytes(format):
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), (35, 69, 103)).save(stream, format=format)
    return stream.getvalue()


def test_rendered_png_uses_common_qt_signature_and_preserves_pixels(tmp_path, monkeypatch):
    original = QtGui.QPixmap
    class CommonPixmap(original):
        def loadFromData(self, data, format=None):
            if format is not None and not isinstance(format, str):
                raise TypeError("New Qt format parameter requires str or None")
            return super().loadFromData(data)
    monkeypatch.setattr(QtGui, "QPixmap", CommonPixmap)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = QtWidgets.QMainWindow()
    controller = BackgroundController(window, SimpleNamespace(
        background_id=tmp_path.name, default_opacity=0.25, default_dim=0.1,
    ))
    try:
        controller.set_rendered_image(image_bytes("PNG"))
        assert controller.active
        assert controller.layer.pixmap.toImage().pixelColor(0, 0).getRgb() == (35, 69, 103, 255)
        with pytest.raises(ValueError, match="PNG"):
            controller.set_rendered_image(image_bytes("JPEG"))
        assert controller.active
        assert controller.layer.pixmap.toImage().pixelColor(0, 0).getRgb() == (35, 69, 103, 255)
    finally:
        controller.close()
        window.close()
        window.deleteLater()
        app.processEvents()

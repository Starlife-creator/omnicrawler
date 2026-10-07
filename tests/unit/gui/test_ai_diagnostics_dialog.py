import threading
import time

from PySide6.QtWidgets import QApplication

from omnicrawler.gui.views import ai_service_center as module


def _wait(predicate, app):
    deadline = time.monotonic() + 10
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_decimal_budget_and_existing_provider_limits_survive_save(tmp_path):
    app = QApplication.instance() or QApplication([])
    config = {"providers": {"default": {"type": "openai_compatible", "base_url": "https://example.org/v1", "model": "chosen",
        "supports_json_schema": True, "pricing": {"input_per_million": 1, "output_per_million": 2}}},
        "budget": {"max_cost": .125, "maximum_requests": 3}}
    dialog = module.AIServiceCenterDialog(config, workspace=tmp_path)
    assert dialog._cost_limit.value() == .125
    dialog._save_and_accept()
    assert config["budget"]["maximum_cost"] == config["budget"]["max_cost"] == .125
    assert config["budget"]["maximum_requests"] == 3
    assert config["providers"]["default"]["supports_json_schema"] is True
    dialog.close()
    app.processEvents()


def test_close_waits_for_owned_probe_and_late_result_is_discarded(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    started, release = threading.Event(), threading.Event()
    def discover(*_args, **_kwargs):
        started.set()
        assert release.wait(5)
        return ["fixture"]
    monkeypatch.setattr(module, "discover", discover)
    dialog = module.AIServiceCenterDialog({}, workspace=tmp_path)
    dialog._base_url.setText("https://example.org/v1")
    dialog._model_name.setText("fixture")
    dialog.show()
    dialog._test_connection()
    assert started.wait(5)
    assert dialog._active_workers
    dialog.reject()
    assert dialog.isVisible() and dialog._close_pending
    release.set()
    _wait(lambda: not dialog._active_workers, app)
    assert not dialog.isVisible()


def test_generation_result_is_readable_and_settings_scroll_in_small_window(tmp_path, monkeypatch):
    from PySide6.QtTest import QSignalSpy, QTest
    from PySide6.QtWidgets import QScrollArea

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(module, "test_generation", lambda *_args, **_kwargs: {
        "accounting": {"network_attempts": 1, "total_tokens": 7, "estimated_cost": .000012},
        "total_tokens": 7, "structured_output_tested": True})
    worker = module.AITestWorker("https://example.org/v1", "", "fixture", 20, tmp_path, generate_options={})
    signal = QSignalSpy(worker.test_done)
    worker.start()
    assert worker.wait(5000)
    app.processEvents()
    assert signal.count() == 1
    message = signal.at(0)[1]
    assert "7 Token" in message and "账单" in message and "JSON Schema" in message
    assert '"accounting"' not in message and len(message) < 200
    dialog = module.AIServiceCenterDialog({}, workspace=tmp_path)
    dialog.resize(760, 580)
    dialog.show()
    QTest.qWait(100)
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    scroll.ensureWidgetVisible(dialog._max_tokens)
    app.processEvents()
    center = dialog._max_tokens.mapTo(scroll.viewport(), dialog._max_tokens.rect().center())
    assert scroll.viewport().rect().contains(center)
    dialog.close()
    app.processEvents()

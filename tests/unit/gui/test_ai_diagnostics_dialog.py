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

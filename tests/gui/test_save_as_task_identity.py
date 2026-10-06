from types import SimpleNamespace

from PySide6.QtWidgets import QFileDialog, QMessageBox

from omnicrawler.gui.core.config_model import CrawlConfig
from omnicrawler.gui.delegates.config_manager import ConfigManager


def test_failed_save_as_preserves_current_task_and_path(tmp_path, monkeypatch):
    import omnicrawler.gui.delegates.config_manager as module
    from omnicrawler.gui.core import config_serializer

    config = CrawlConfig(project_name="A", workspace="work/A")
    old_path = tmp_path / "old.yaml"
    new_path = tmp_path / "new.yaml"
    window = SimpleNamespace(_config=config, _config_path=old_path, _project_root=tmp_path,
                             _config_history=SimpleNamespace(snapshot=lambda *a, **k: None))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new_path), ""))
    if hasattr(module, "QInputDialog"):
        monkeypatch.setattr(module.QInputDialog, "getItem", lambda *a, **k: ("同一任务修订（保留身份）", True))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(config_serializer, "save_yaml", fail)
    ConfigManager(window).save_config_as()
    assert window._config_path == old_path
    assert window._config is config


def test_independent_copy_gets_new_identity_and_workspace(tmp_path, monkeypatch):
    import omnicrawler.gui.delegates.config_manager as module
    from omnicrawler.gui.core import config_serializer

    config = CrawlConfig(project_name="A", workspace="work/A")
    config.passthrough["project"] = {"identity_origin": "legacy_config_path"}
    new_path = tmp_path / "copy.yaml"
    saved = []
    window = SimpleNamespace(_config=config, _config_path=tmp_path / "old.yaml", _project_root=tmp_path,
                             _config_history=SimpleNamespace(snapshot=lambda *a, **k: None),
                             _bind_application_controllers=lambda: None,
                             _config_label=SimpleNamespace(setText=lambda *a: None),
                             _settings=SimpleNamespace(add_recent_file=lambda *a: None))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(new_path), ""))
    monkeypatch.setattr(module.QInputDialog, "getItem", lambda *a, **k: ("独立任务副本（新身份与工作区）", True))
    monkeypatch.setattr(config_serializer, "save_yaml", lambda value, path: saved.append((value, path)))
    monkeypatch.setattr(ConfigManager, "refresh_recent_menu", lambda self: None)
    monkeypatch.setattr(module.ToastManager, "instance", lambda: SimpleNamespace(success=lambda *a: None))
    ConfigManager(window).save_config_as()
    assert saved[0][1] == new_path
    assert window._config.task_id != config.task_id
    assert window._config.workspace != config.workspace
    assert config.workspace == "work/A"
    assert window._config_path == new_path
    assert "identity_origin" not in window._config.passthrough["project"]
    assert config.passthrough["project"]["identity_origin"] == "legacy_config_path"

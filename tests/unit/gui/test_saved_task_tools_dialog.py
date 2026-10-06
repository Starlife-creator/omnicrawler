from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from omnicrawler.gui.views import task_tools as tools
from omnicrawler.services.task_tools import repair_binding


@pytest.fixture
def dialog(tmp_path):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: tools, workspace: work}\nsource: {kind: static_html, seeds: [https://example.test/]}\n", encoding="utf-8")
    state = {"path": config, "token": "saved", "reloads": 0}
    def reload_config():
        state["reloads"] += 1
    window = tools.TaskToolsDialog(config, lambda: state["path"], lambda: state["token"], reload_config, parent)
    yield window, state
    if window._worker is not None:
        window._worker.requestInterruption()
        assert window._worker.wait(5000)
        QTest.qWait(50)
    window.close()
    parent.close()
    app.processEvents()


def test_selection_is_explicit_and_ai_is_opt_in(dialog, monkeypatch):
    window, _ = dialog
    calls = []
    monkeypatch.setattr(window, "_launch", lambda *args: calls.append(args))
    assert window.tabs.count() == 7 and not window.use_ai.isChecked()
    window._analyze()
    window._retry()
    assert not calls
    window._done("sources", {}, {"sources": [{"id": "chosen", "path": "chosen.pdf"},
        {"id": "private", "path": "private.pdf"}], "manifest_sha256": "verified"})
    assert window._checked(window.sources) == []
    window.sources.item(0).setCheckState(Qt.CheckState.Checked)
    window._analyze()
    assert not calls
    window.manifest.setText("manifest.json")
    window.report_output.setText("report")
    window._analyze()
    assert calls[0][0] == "analyze"
    assert calls[0][1]["selected_ids"] == ["chosen"] and calls[0][1]["use_ai"] is False
    assert calls[0][1]["manifest_sha256"] == "verified"


def test_authentication_failure_cannot_be_retried(dialog, monkeypatch):
    window, _ = dialog
    calls = []
    monkeypatch.setattr(window, "_launch", lambda *args: calls.append(args))
    monkeypatch.setattr(window, "_confirm", lambda: True)
    window._done("failures", {}, {"failures": [{"fingerprint": "auth", "url": "https://example.test/", "reason": "session_expired"},
        {"fingerprint": "selected", "url": "https://example.test/item", "reason": "request_failed"}]})
    assert not window.failures.item(0).flags() & Qt.ItemFlag.ItemIsEnabled
    window.failures.item(0).setCheckState(Qt.CheckState.Checked)
    window.failures.item(1).setCheckState(Qt.CheckState.Checked)
    window._retry()
    assert calls == [("retry", {"fingerprints": ["selected"], "confirmed": True})]


def test_changed_task_blocks_dispatch_and_stale_reload(dialog):
    window, state = dialog
    state["token"] = "edited"
    window._launch("failures", {})
    assert window._worker is None
    window._done("repair:apply", {}, {"status": "applied"})
    assert state["reloads"] == 0
    state["token"] = "saved"
    state["path"] = None
    window._done("repair:rollback", {}, {"status": "rolled_back"})
    assert state["reloads"] == 0


def test_candidate_change_requires_new_preview(dialog, tmp_path, monkeypatch):
    window, _ = dialog
    evidence, candidate = tmp_path / "evidence.json", tmp_path / "candidate.json"
    evidence.write_text("{}")
    candidate.write_text("{}")
    window.evidence.setText(str(evidence))
    window.candidate.setText(str(candidate))
    binding = repair_binding(evidence, candidate)[0]
    window._done("repair:preview", {}, {"status": "preview", "preview_binding": binding,
        "candidates": [{"candidate": {"field": "title", "old_rule": ".old", "new_rule": ".new"},
                        "comparison": {"improves_safely": True}}]})
    assert window._preview_binding == binding
    calls = []
    monkeypatch.setattr(window, "_launch", lambda *args: calls.append(args))
    monkeypatch.setattr(window, "_confirm", lambda: True)
    candidate.write_text('{"edited": true}')
    window._repair("apply")
    assert not calls


def test_close_waits_for_worker_and_is_idempotent(dialog, monkeypatch):
    window, state = dialog
    entered, release = threading.Event(), threading.Event()
    def work(action):
        entered.set()
        assert release.wait(5)
        return {"status": "applied"}
    monkeypatch.setattr(tools, "execute", work)
    window.show()
    window._launch("repair:apply", {})
    worker = window._worker
    try:
        assert entered.wait(5)
        window.reject()
        window.reject()
        assert worker is not None and worker.isRunning() and window.isVisible()
        assert window._close_pending and worker.isInterruptionRequested()
    finally:
        release.set()
        assert worker is not None and worker.wait(5000)
        QTest.qWait(100)
    assert window._worker is None and not window.isVisible()
    assert state["reloads"] == 0


def test_failed_worker_restores_controls(dialog, monkeypatch):
    window, state = dialog
    def work(action):
        raise ValueError("injected failure")
    monkeypatch.setattr(tools, "execute", work)
    window._launch("failures", {})
    worker = window._worker
    assert worker is not None and worker.wait(5000)
    QTest.qWait(100)
    assert window._worker is None and window.tabs.isEnabled()
    assert "injected failure" in window.result_view.toPlainText() and state["reloads"] == 0


def test_component_requires_review_and_confirmation_and_clears_changed_package(dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, _ = dialog
    calls = []
    monkeypatch.setattr(window, "_launch", lambda *args: calls.append(args))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window._manage_component("import")
    assert calls == []
    window.component_package.setText("reviewed.ocp")
    window._done("components:inspect", {"package": "reviewed.ocp"}, {
        "component": {"name": "ocr", "version": "1.0", "purpose": "中文OCR", "disk_bytes": 123,
                      "uninstall_impact": "扫描页不可用"}, "compatible": True,
        "package_sha256": "reviewed", "registry_sha256": "state"})
    assert "扫描页不可用" in window.component_details.toPlainText()
    window._manage_component("import")
    assert calls[0][0] == "components:import"
    assert calls[0][1]["package_sha256"] == "reviewed"
    window.component_package.setText("changed.ocp")
    window._manage_component("import")
    assert len(calls) == 1


def test_component_inspection_cannot_rebind_old_install_list(dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, _ = dialog
    calls = []
    monkeypatch.setattr(window, "_launch", lambda *args: calls.append(args))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window._done("components:list", {}, {"components": [{"name": "ocr", "version": "1.0"}], "registry_sha256": "old"})
    window.components.setCurrentRow(0)
    window.component_package.setText("new.ocp")
    window._done("components:inspect", {"package": "new.ocp"}, {"component": {"name": "ocr", "version": "2.0"},
        "compatible": True, "package_sha256": "new-package", "registry_sha256": "new-state"})
    window._manage_component("uninstall")
    assert calls[0][1]["registry_sha256"] == "old"


def test_workspace_import_requires_explicit_destination_and_confirmation(dialog, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, _ = dialog
    calls = []
    monkeypatch.setattr(window, "_launch", lambda *args: calls.append(args))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window._import_workspace()
    assert not calls
    window.workspace_package.setText("reviewed.zip")
    window.workspace_destination.setText("new workspace")
    window._import_workspace()
    assert calls == [("workspace:import", {"target": "reviewed.zip", "destination": "new workspace", "confirmed": True})]
    window._done("workspace:import", {}, {"config": "new workspace/config.yaml", "unresolved_references": [{"reason": "missing"}]})
    assert "1" in window.result_view.toPlainText()
    assert window.open_report.isEnabled()


def test_forced_deletion_waits_for_owned_task_worker(tmp_path, monkeypatch):
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    config = tmp_path / "task.yaml"
    config.write_text("project: {name: lifecycle}\n")
    window = tools.TaskToolsDialog(config, lambda: config, lambda: "saved", lambda: None, parent)
    entered, release = threading.Event(), threading.Event()
    def work(action):
        entered.set()
        assert release.wait(5)
        return {"components": [], "registry_sha256": "state"}
    monkeypatch.setattr(tools, "execute", work)
    window._launch("components:list", {})
    worker = window._worker
    try:
        assert entered.wait(5)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert shiboken6.isValid(window)
        assert worker.isInterruptionRequested()
    finally:
        release.set()
        assert worker.wait(5000)
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(window)
    parent.close()


def test_task_identity_ignores_serializer_time_and_does_not_mutate_model(monkeypatch):
    from omnicrawler.gui.core import config_serializer
    from omnicrawler.gui.core.config_model import CrawlConfig
    calls = []
    model = CrawlConfig()
    def serialize(config):
        calls.append(config)
        config.project_name = "mutated copy"
        return f"# generated {len(calls)}\nproject: {{name: stable}}\n"
    monkeypatch.setattr(config_serializer, "to_yaml", serialize)
    assert tools._config_token(model) == tools._config_token(model)
    assert all(config is not model for config in calls)
    assert model.project_name != "mutated copy"


def test_workflow_stage_selection_shows_diagnostic_guidance(dialog):
    window, _state = dialog
    window._launch("workflow", {})
    worker = window._worker
    assert worker is not None and worker.wait(5000)
    QTest.qWait(100)
    assert window.workflow_steps.count() >= 4
    window.workflow_steps.setCurrentRow(window.workflow_steps.count() - 1)
    assert "交付" in window.workflow_details.toPlainText()
    assert "尚无试跑" in window.result_view.toPlainText()

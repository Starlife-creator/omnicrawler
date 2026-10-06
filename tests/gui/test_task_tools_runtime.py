"""Task diagnostics continue using the dialog's owned-worker and stale-result guards."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from omnicrawler.gui.views.task_tools import TaskToolsDialog


def test_runtime_display_and_stale_result_guard(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: example}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    parent = QWidget()
    current = [path]
    dialog = TaskToolsDialog(path, lambda: current[0], lambda: "task", lambda: None, parent)
    report = {"stages": [{"title": "Fetch", "detail": "configured", "check": "retry", "runtime_status": "failed", "runtime_detail": {"error_types": {"ValueError": 1}}}],
              "runtime": {"run_id": "original", "status": "partial_success", "config_match": "matching"}}
    dialog._done("workflow", {}, report)
    assert "存在失败" in dialog.workflow_steps.item(0).text()
    assert "ValueError" in dialog.workflow_details.toPlainText()
    current[0] = tmp_path / "other.yaml"
    dialog._done("workflow", {}, {**report, "runtime": {"run_id": "late"}})
    assert dialog.run_id.text() == "original"
    dialog._close_pending = True
    dialog._done("workflow", {}, report)
    assert dialog._worker is None
    dialog.close()
    parent.close()
    app.processEvents()


def test_suppressed_notice_explains_reason_and_cannot_be_retried(tmp_path):
    from PySide6.QtCore import Qt

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: example}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    parent = QWidget()
    dialog = TaskToolsDialog(path, lambda: path, lambda: "task", lambda: None, parent)
    dialog._done("notifications:report", {}, {"enabled": True, "deliveries": [{
        "event_id": "suppressed", "status": "suppressed", "attempts": 0,
        "suppression_reason": "relative_baseline_zero",
    }]})
    item = dialog.notifications.item(0)
    assert "已抑制" in item.text()
    assert "原值为零" in item.toolTip()
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    dialog.close()
    parent.close()
    app.processEvents()

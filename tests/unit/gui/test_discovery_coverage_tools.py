from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt

from tests.unit.gui.test_saved_task_tools_dialog import dialog as dialog


def report():
    return {'run_id': 'observed-run', 'known_discovery_gaps': 2, 'workspace_unfinished_requests': 3,
            'historical_gap_paths': 4, 'gap_paths_total': 200, 'offset': 0, 'next_offset': 100,
            'gap_paths': [
                {'url': 'https://example.org/a', 'decision': 'depth_limit', 'reason': 'crawl.max_depth',
                 'parent_url': 'https://example.org/', 'parent_fingerprint': 'parent'},
                {'url': 'https://example.org/b', 'decision': 'scope_rejected', 'reason': 'deny_patterns',
                 'parent_url': 'https://example.org/', 'parent_fingerprint': 'parent'}]}


def test_gui_coverage_exposes_unknown_scope_and_only_revisits_selected_parent(dialog, monkeypatch):
    window, _ = dialog
    calls = []
    monkeypatch.setattr(window, '_launch', lambda *args: calls.append(args))
    monkeypatch.setattr(window, '_confirm', lambda: True)
    window._done('coverage', {}, report())
    assert window.discovery_gaps.count() == 2
    assert '未知' in window.result_view.toPlainText()
    window._retry_discovery()
    assert not calls
    for index in range(2):
        window.discovery_gaps.item(index).setCheckState(Qt.CheckState.Checked)
    window._retry_discovery()
    assert calls == [('retry-discovery', {'fingerprints': ['parent'], 'run_id': 'observed-run', 'confirmed': True})]
    window._next_coverage()
    assert calls[-1] == ('coverage', {'run_id': 'observed-run', 'offset': 100})
    window._done('retry-discovery', {}, {'queued': 1})
    assert window.discovery_gaps.count() == 0 and not window._coverage_run_id
    window._retry_discovery()
    assert len(calls) == 2


def test_gui_stale_task_cannot_load_another_tasks_coverage(dialog):
    window, state = dialog
    state['token'] = 'changed'
    window._done('coverage', {}, report())
    assert not window._coverage_run_id and window.discovery_gaps.count() == 0


def test_terminal_worker_warns_about_known_gaps_without_claiming_full_site_coverage(dialog, tmp_path):
    from omnicrawler.gui.runner.worker_task_runner import WorkerTaskRunner

    runner = WorkerTaskRunner(project_root=tmp_path)
    runner._backend = SimpleNamespace(status=lambda: {'status': 'succeeded', 'records': 1,
        'coverage': {'observed_traversal': 'partial', 'known_discovery_gaps': 2,
                     'workspace_unfinished_requests': 3, 'historical_gap_paths': 4}})
    lines = []
    runner.log_line.connect(lambda message, level: lines.append((message, level)))
    runner._poll()
    assert any(level == 'warn' and '发现' in message and '未知' in message for message, level in lines)
    assert not runner._poller.isActive()

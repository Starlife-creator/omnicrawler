import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from omnicrawler import runtime_paths


def test_source_application_dir_is_project_root() -> None:
    assert (runtime_paths.application_dir() / "pyproject.toml").is_file()


def test_source_user_guide_is_found() -> None:
    # 用户指南已合并为 OmniCrawler-用户指南.md，位于项目根目录
    assert runtime_paths.find_document("OmniCrawler-用户指南.md", "USER_GUIDE.md") == (
        runtime_paths.application_dir() / "OmniCrawler-用户指南.md"
    )


def test_frozen_build_prefers_companion_cli(tmp_path: Path) -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    gui = tmp_path / f"OmniCrawler{suffix}"
    cli = tmp_path / f"omnicrawler{suffix}"
    gui.touch()
    cli.touch()
    with (
        patch.object(runtime_paths.sys, "frozen", True, create=True),
        patch.object(runtime_paths.sys, "executable", str(gui)),
    ):
        assert runtime_paths.resolve_cli_command("omnicrawler") == str(cli)


def test_frozen_document_is_found_next_to_executable(tmp_path: Path) -> None:
    gui = tmp_path / "OmniCrawler.exe"
    guide = tmp_path / "docs" / "USER_GUIDE.md"
    guide.parent.mkdir()
    guide.write_text("guide", encoding="utf-8")
    with (
        patch.object(runtime_paths.sys, "frozen", True, create=True),
        patch.object(runtime_paths.sys, "executable", str(gui)),
    ):
        assert runtime_paths.find_document("USER_GUIDE.md") == guide


def test_resource_monitor_counts_worker_process_tree_rss(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("psutil")
    from omnicrawler.gui.widgets import resource_monitor

    class FakeProcess:
        def __init__(self, pid: int, rss: int, children: list["FakeProcess"] | None = None) -> None:
            self.pid = pid
            self._rss = rss
            self._children = children or []

        def children(self, recursive: bool = False) -> list["FakeProcess"]:
            assert recursive
            return self._children

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=self._rss)

    child = FakeProcess(2, 30)
    root = FakeProcess(1, 70, [child, child])
    monkeypatch.setattr(resource_monitor.psutil, "Process", lambda _pid: root)

    assert resource_monitor._process_tree_rss(1) == 100


def test_resource_monitor_clears_memory_when_pid_is_removed() -> None:
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.widgets.resource_monitor import ResourceMonitor

    app = QApplication.instance() or QApplication([])
    monitor = ResourceMonitor()
    monitor._mem_label.setText("内存: 128 MB")
    monitor.set_pid(None)

    assert monitor._mem_label.text() == "内存: --"
    monitor.deleteLater()
    app.processEvents()

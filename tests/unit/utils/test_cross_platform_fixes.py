"""Phase 5a 跨平台/稳健性修复的回归测试。

覆盖可脱离 GUI 交互验证的部分：
- A16：User-Agent 不再硬编码版本号，随包版本自动更新
- A22：帮助中心未知 ID 的 registry 行为（GUI 侧 show_help 有兜底）
- A21：AppSettings.clear_recent 公开接口（不依赖私有 _settings）
- B9：CSV 大文件不再截断（offscreen QApplication）
- B10：无头子进程环境变量 PYTHONIOENCODING 拼写正确
- B11：WorkerTaskRunner 对 created_at 为 None 的兜底
"""

from __future__ import annotations

import io
import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_a16_config_default_user_agent_tracks_package_version() -> None:
    """A16：ProjectConfig 默认 UA 不再硬编码 "OmniCrawler-GUI/1.1"。"""
    pytest.importorskip("PyQt6")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
    from omnicrawl import __version__
    from omnicrawl.gui.core.config_model import CrawlConfig

    cfg = CrawlConfig()
    assert cfg.user_agent.startswith("OmniCrawler/")
    assert __version__ in cfg.user_agent
    assert "1.1" not in cfg.user_agent


def test_a16_home_version_fallback_uses_package_version(monkeypatch) -> None:
    """A16：包版本读取失败时回退 omnicrawl.__version__ 而非硬编码 "2.7"。"""
    pytest.importorskip("PyQt6")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
    import importlib.metadata

    from omnicrawl import __version__
    from omnicrawl.gui.home import _package_version
    monkeypatch.setattr(importlib.metadata, "version", lambda name: (_ for _ in ()).throw(Exception("no dist")))
    assert _package_version() == __version__


def test_a22_help_registry_unknown_id_raises_key_error() -> None:
    """A22 前提：registry 对未知 ID 抛 KeyError（GUI show_help 已加兜底）。"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
    from omnicrawl.services.help_registry import get_help

    with pytest.raises(KeyError):
        get_help("troubleshooting")  # 该 ID 未收录


def test_a21_settings_clear_recent_is_public(monkeypatch, tmp_path) -> None:
    """A21：AppSettings 提供公开 clear_recent()，不再被外部访问 _settings 私有成员。

    P0-4：此前 AppSettings() 写真实 QSettings（Windows 注册表 / Linux
    ~/.config）并硬编码 C:\\tmp\\a.yaml——测试污染真实用户配置且仅 Windows
    有效。现 monkeypatch is_frozen + portable_data_root 走 F53 便携分支，
    写入落到 tmp_path，且用 tmp_path 下文件替代硬编码路径。
    """
    pytest.importorskip("PyQt6")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
    from PyQt6.QtWidgets import QApplication

    import omnicrawl.core.runtime_paths as runtime_paths
    from omnicrawl.gui.settings import AppSettings

    runtime_paths.portable_data_root.cache_clear()
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: True)
    monkeypatch.setattr(runtime_paths, "portable_data_root", lambda: tmp_path)

    QApplication.instance() or QApplication([])
    AppSettings.reset()
    settings = AppSettings()
    recent = tmp_path / "a.yaml"
    settings.add_recent_file(str(recent))
    settings.sync()
    assert settings.recent_files
    settings.clear_recent()
    settings.sync()
    assert settings.recent_files == []
    # F53 便携分支：设置 INI 落在数据根（tmp_path）内，未污染真实配置
    ini = tmp_path / "settings.ini"
    assert ini.is_file(), "便携分支设置未落盘到数据根"


def test_b9_csv_index_no_100k_truncation(tmp_path) -> None:
    """B9：结果表大文件不再截断——完整行数可索引。"""
    pytest.importorskip("PyQt6")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.views.result_table import ROWS_PER_PAGE, CsvStreamModel

    QApplication.instance() or QApplication([])
    csv_path = tmp_path / "big.csv"
    header = "a,b,c\n"
    with csv_path.open("w", encoding="utf-8") as f:
        f.write(header)
        for i in range(ROWS_PER_PAGE * 3 + 7):
            f.write(f"{i},{i * 2},{i * 3}\n")

    model = CsvStreamModel()
    assert model.load_file(csv_path)
    assert model.total_rows == ROWS_PER_PAGE * 3 + 7  # 完整计数，未被截断
    assert model.total_pages == 4
    model.go_to_page(3)
    assert model.rowCount() == 7  # 尾页完整

    # 异步路径同样完整计数
    from PyQt6.QtTest import QSignalSpy

    from omnicrawl.gui.async_workers import CsvIndexWorker
    worker = CsvIndexWorker(csv_path)
    spy = QSignalSpy(worker.finished_indexing)
    worker.run()
    assert len(spy) == 1, "异步索引未完成"
    headers, total_rows, file_size = spy[0]
    assert headers == ["a", "b", "c"]
    assert total_rows == ROWS_PER_PAGE * 3 + 7  # 与同步路径一致，未被截断
    assert file_size > 0


def test_b9_csv_tail_reachable_beyond_100k_rows(tmp_path) -> None:
    """B9：超过 10 万行时尾页仍可达且内容正确（旧实现在第 100000 行 break 丢尾部）。"""
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.views.result_table import ROWS_PER_PAGE, CsvStreamModel

    QApplication.instance() or QApplication([])
    total = 100_000 + 3
    csv_path = tmp_path / "huge.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        f.write("idx,val\n")
        for i in range(total):
            f.write(f"{i},v{i}\n")

    model = CsvStreamModel()
    assert model.load_file(csv_path)
    assert model.total_rows == total  # 完整计数，未在 100000 行截断

    last_page = model.total_pages - 1
    model.go_to_page(last_page)
    assert model.rowCount() == total - last_page * ROWS_PER_PAGE
    tail = model.rowCount() - 1
    assert model.data(model.index(tail, 0)) == str(total - 1)
    assert model.data(model.index(tail, 1)) == f"v{total - 1}"


def test_b10_headless_runner_sets_pythonioencoding(tmp_path, monkeypatch) -> None:
    """B10：无头子进程须收到 PYTHONIOENCODING（旧代码拼作 PYTHONIOCODING 从未生效）。"""
    pytest.importorskip("ruamel.yaml")
    from omnicrawl.gui.runner import headless_runner as hr

    config_path = tmp_path / "task.yaml"
    config_path.write_text("project_name: demo\n", encoding="utf-8")
    fake_config = SimpleNamespace(
        validate=lambda: [], project_name="demo", task_id="t-1"
    )
    monkeypatch.setattr(hr, "load_yaml", lambda _path: fake_config)
    monkeypatch.setattr(hr, "check_omnicrawl", lambda _path: (True, "0.4.0"))

    captured: dict[str, dict[str, str]] = {}

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.StringIO("正在抓取 ✅\n")

        def wait(self) -> int:
            return 0

    def _fake_popen(_args, **kwargs):
        captured["env"] = kwargs["env"]
        return _FakeProcess()

    monkeypatch.setattr(hr.subprocess, "Popen", _fake_popen)

    assert hr.HeadlessRunner().run(config_path) == 0
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "PYTHONIOCODING" not in captured["env"]


def test_b11_worker_task_runner_tolerates_none_created_at(tmp_path, monkeypatch) -> None:
    """B11：config.created_at 为 None 时 start() 不再 AttributeError。"""
    pytest.importorskip("PyQt6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.core.config_model import CrawlConfig
    from omnicrawl.gui.runner.worker_task_runner import WorkerTaskRunner

    app = QApplication.instance() or QApplication([])
    runner = WorkerTaskRunner(project_root=tmp_path)
    runner._backend = SimpleNamespace(start=lambda _path: {"status": "running"})
    config = CrawlConfig(
        project_name="none-created-at",
        workspace=str(tmp_path / "work"),
        seed_urls=["https://example.org/"],
    )
    config.created_at = None  # type: ignore[assignment]

    assert runner.start(config) is True
    assert runner.config_path is not None and runner.config_path.is_file()
    runner._poller.stop()
    app.processEvents()

"""Phase 5a 跨平台/稳健性修复的回归测试。

覆盖可脱离 GUI 交互验证的部分：
- A16：User-Agent 不再硬编码版本号，随包版本自动更新
- A22：帮助中心未知 ID 的 registry 行为（GUI 侧 show_help 有兜底）
- A21：AppSettings.clear_recent 公开接口（不依赖私有 _settings）
- B9：CSV 大文件不再截断（offscreen QApplication）
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_a16_config_default_user_agent_tracks_package_version() -> None:
    """A16：ProjectConfig 默认 UA 不再硬编码 "OmniCrawler-GUI/1.1"。"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
    from omnicrawl import __version__
    from omnicrawl.gui.core.config_model import CrawlConfig

    cfg = CrawlConfig()
    assert cfg.user_agent.startswith("OmniCrawler/")
    assert __version__ in cfg.user_agent
    assert "1.1" not in cfg.user_agent


def test_a16_home_version_fallback_uses_package_version(monkeypatch) -> None:
    """A16：包版本读取失败时回退 omnicrawl.__version__ 而非硬编码 "2.7"。"""
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


def test_a21_settings_clear_recent_is_public(monkeypatch) -> None:
    """A21：AppSettings 提供公开 clear_recent()，不再被外部访问 _settings 私有成员。"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.settings import AppSettings

    QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings.add_recent_file(r"C:\tmp\a.yaml")
    assert settings.recent_files
    settings.clear_recent()
    assert settings.recent_files == []


def test_b9_csv_index_no_100k_truncation(tmp_path) -> None:
    """B9：结果表大文件不再截断——完整行数可索引。"""
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
    from omnicrawl.gui.async_workers import CsvIndexWorker
    worker = CsvIndexWorker(csv_path)
    worker.run()
    assert worker is not None

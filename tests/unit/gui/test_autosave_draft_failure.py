"""草稿恢复失败必须**可诊断、有反馈**。

为什么要有这个文件（2026-11-25 实测）：

`AutosaveManager.load_draft` 此前是 `except Exception: return None`，
什么都不记；调用方 `main.py::_on_draft_found` 只做 `if config:`，
**没有 else**。于是用户点了「恢复草稿」、草稿却坏了/被删时：

```
对话框关闭 → 界面毫无变化 → 没有错误、没有提示、日志也没有一行
```

用户只会觉得「点了没反应」。两种真实成因都返回 None：

* 草稿文件不存在（并发清理 / 手动删除）；
* YAML 解析失败（自动保存写到一半时断电，草稿被截断）。

现在：原因写进日志，调用方给出可见提示。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicrawler.gui.core.autosave import AutosaveManager  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _manager(tmp_path: Path) -> AutosaveManager:
    return AutosaveManager(tmp_path)


def test_corrupt_yaml_is_logged_not_silent(tmp_path: Path, qapp, caplog) -> None:
    """坏 YAML：返回 None，但必须留下 warning 且带上路径与原因。"""
    draft = tmp_path / "bad.yaml"
    draft.write_text("source: [unclosed", encoding="utf-8")
    manager = _manager(tmp_path)

    with caplog.at_level(logging.WARNING):
        result = manager.load_draft(draft)

    assert result is None
    assert caplog.records, "草稿加载失败却没有任何日志 —— 用户和我们都无从排查"
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert str(draft) in text, f"日志没说是哪个文件：{text!r}"
    assert "草稿加载失败" in text, text


def test_missing_file_is_logged_not_silent(tmp_path: Path, qapp, caplog) -> None:
    """文件不存在同样要留痕（否则与「内容损坏」无法区分）。"""
    manager = _manager(tmp_path)
    missing = tmp_path / "nope.yaml"

    with caplog.at_level(logging.WARNING):
        result = manager.load_draft(missing)

    assert result is None
    assert caplog.records
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert str(missing) in text


def test_successful_load_does_not_warn(tmp_path: Path, qapp, caplog) -> None:
    """正常加载不得产生 warning —— 否则日志会被噪音淹没。"""
    from omnicrawler.gui.core.config_model import CrawlConfig
    from omnicrawler.gui.core.config_serializer import to_yaml

    draft = tmp_path / "ok.yaml"
    draft.write_text(to_yaml(CrawlConfig()), encoding="utf-8")
    manager = _manager(tmp_path)

    with caplog.at_level(logging.WARNING):
        result = manager.load_draft(draft)

    assert result is not None, "正常草稿没有加载成功"
    assert not caplog.records, f"正常加载却报了警告：{[r.getMessage() for r in caplog.records]}"

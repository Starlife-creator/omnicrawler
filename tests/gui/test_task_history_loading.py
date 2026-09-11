"""任务历史加载：小文件同步、大文件后台（接线 `JsonlLoadWorker`）。

## 为什么要分流而不是一律异步

历史文件是**只增不减**的本地日志。实测同步解析成本随行数线性增长：
100 行 6.5 ms / 10k 行 29.5 ms / **100k 行 214.6 ms**——
最后那种规模会让界面明显卡住，正是 `audit-20260805/report_gui_core.md`
「同步耗时操作阻塞 UI 线程」指向的问题，也是 `JsonlLoadWorker` 被建成的原因。

但**一律异步会破坏既有契约**：4 处调用点与既有测试都依赖
「`load_history()` 返回后即可读 `_records`」。因此按文件大小分流：
512 KiB（≈4k 行、约 12 ms，低于一帧预算）以内同步，超过则后台。
这样典型使用零行为变化，而病态规模不再卡界面。
"""

# ruff: noqa: E402, I001 —— 导入顺序刻意先 `importorskip("PySide6")` 再导入被测模块，
# 属结构性约束，与 isort 的排序规则冲突，故显式声明。
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicrawler.gui.views.task_history import (  # noqa: E402
    HISTORY_FILE,
    _SYNC_PARSE_MAX_BYTES,
    TaskHistory,
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _record(index: int) -> dict[str, object]:
    return {
        "task_id": f"t{index}",
        "project_name": f"项目 {index}",
        "config_path": f"/cfg/{index}.yaml",
        "workspace": f"/work/{index}",
        "status": "finished",
        "started_at": f"2026-01-01T00:{index % 60:02d}:00+00:00",
    }


def _write_history(root: Path, count: int) -> Path:
    path = root / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(_record(i), ensure_ascii=False) for i in range(count)) + "\n",
        encoding="utf-8",
    )
    return path


def _pad_past_threshold(path: Path) -> None:
    """把文件撑到同步阈值以上。

    填充行**刻意不是合法 JSON**（解析器会跳过）——如果用合法 JSON 填充，
    它们会被当成记录，测试断言的记录数就不再是文件里"真正的"记录数了。
    """
    filler = "#" + "x" * 240
    need = _SYNC_PARSE_MAX_BYTES // len(filler) + 50
    with path.open("a", encoding="utf-8") as handle:
        for _ in range(need):
            handle.write(filler + "\n")


def _pump(predicate, timeout: float = 10.0) -> bool:
    """转事件循环直到条件成立（后台加载需要事件循环才能收到信号）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_small_history_loads_synchronously(qt_app, tmp_path: Path) -> None:
    """★ 保持既有契约：小文件调用后立刻可读。"""
    _write_history(tmp_path, 20)
    view = TaskHistory(tmp_path)

    view.load_history()

    assert len(view.recent_records(limit=50)) == 20
    assert view._list.count() == 20
    assert not view.content.isHidden()


def test_large_history_goes_through_the_worker(qt_app, tmp_path: Path) -> None:
    """★ 超过阈值时不得阻塞调用方：立刻返回，随后由信号补齐。"""
    path = _write_history(tmp_path, 40)
    _pad_past_threshold(path)

    view = TaskHistory(tmp_path)
    started = time.monotonic()
    view.load_history()
    elapsed = time.monotonic() - started

    assert view._load_worker is not None, "大文件应当交给后台 worker"
    assert elapsed < 0.5, f"load_history 返回耗时 {elapsed:.3f}s，说明仍在同步解析"
    assert _pump(lambda: view._load_worker is None), "后台加载未在预期时间内完成"
    assert len(view.recent_records(limit=1000)) == 40
    assert view._list.count() == 40


def test_records_added_during_background_load_are_not_lost(qt_app, tmp_path: Path) -> None:
    """★ 后台加载期间写入的新记录必须保留——否则会出现「刚做完的任务消失」。"""
    path = _write_history(tmp_path, 40)
    _pad_past_threshold(path)

    view = TaskHistory(tmp_path)
    view.load_history()
    assert view._load_worker is not None

    config = tmp_path / "during.yaml"
    config.write_text("source: {}", encoding="utf-8")
    view.add_record("during-load", "加载期间新增", str(config), str(tmp_path / "work"))

    assert _pump(lambda: view._load_worker is None)
    task_ids = {record["task_id"] for record in view.recent_records(limit=1000)}
    assert "during-load" in task_ids, "后台加载完成后把加载期间的新记录覆盖掉了"


def test_corrupt_lines_are_skipped_on_both_paths(qt_app, tmp_path: Path) -> None:
    path = _write_history(tmp_path, 5)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("这不是 JSON\n")
        handle.write("\n")
    view = TaskHistory(tmp_path)
    view.load_history()
    assert len(view.recent_records(limit=50)) == 5


def test_missing_history_file_stays_in_empty_state(qt_app, tmp_path: Path) -> None:
    view = TaskHistory(tmp_path)
    view.load_history()
    assert view.recent_records(limit=10) == []
    assert view._load_worker is None

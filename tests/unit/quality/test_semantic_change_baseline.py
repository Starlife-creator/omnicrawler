"""首个同步周期的「基线」标注（方案 C）：数据层只标事实，提示与否交给消费方。

用户裁决（2026-09-13，方案 C）：首轮同步把初始记录记为 `added` **仍然照记**，
但要带上 `baseline` 标记；**是否提示用户由 UI / 通知层决定** —— 数据层不替用户判断
"是否打扰"。因此本文件验证三件事：

1. **首轮**：变更被标 `baseline`；`semantic_changes`（完整事实）照旧如实记录，
   而 `notifiable_changes(report)`（要提示的场景）为空——不被首轮刷屏；
2. **次轮发生真实变化**：不再标 `baseline`，`notifiable_changes` 如实反映；
   同时 `semantic_changes` 仍包含全部事实（报表 / 看板不受影响）；
3. **旧工作区迁移**：缺 `baseline` 列的库打开后自动补列，历史行默认 0（非基线）。
"""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.quality.quality_report import build_quality_report, notifiable_changes
from omnicrawler.state import StateStore

_URL = "https://example.com/item"


def _config(tmp_path: Path) -> AppConfig:
    raw = copy.deepcopy(DEFAULTS)
    raw["project"] = {"name": "baseline", "workspace": str(tmp_path / "workspace")}
    raw["source"] = {"kind": "static_html", "seeds": [_URL]}
    path = tmp_path / "config.yaml"
    path.write_text("project:\n  name: baseline\n", encoding="utf-8")
    return AppConfig(path, tmp_path, raw, tmp_path / "workspace")


def _records(spec: dict[int, str]) -> list[ExtractedRecord]:
    return [
        ExtractedRecord(_URL, "item", {"id": rid, "title": title})
        for rid, title in spec.items()
    ]


def test_first_cycle_is_marked_baseline_and_not_notifiable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.workspace.mkdir()
    request = CrawlRequest(_URL)
    with StateStore(config.workspace / "state.sqlite3") as state:
        first = state.start_run("baseline", str(config.path))
        records = _records({1: "t1", 2: "t2", 3: "t3"})
        changes = state.track_semantic_changes(first, records)
        assert [item["change_type"] for item in changes] == ["added"] * 3
        assert all(item["baseline"] is True for item in changes), "首轮新增必须标 baseline"
        state.save_records(first, request, records)

        report = build_quality_report(config, state, first)

    assert report["semantic_changes"] == {"added": 3}, "完整事实仍要如实记录"
    assert report["semantic_changes_baseline"] == {"added": 3}, "并单独标出其中属基线的部分"
    assert notifiable_changes(report) == {}, "首轮基线不应产生「需要提示的变化」"


def test_later_cycle_is_not_baseline_and_is_notifiable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.workspace.mkdir()
    request = CrawlRequest(_URL)
    with StateStore(config.workspace / "state.sqlite3") as state:
        first = state.start_run("baseline", str(config.path))
        initial = _records({1: "t1", 2: "t2"})
        # 必须调 track_semantic_changes 才会写入 record_versions —— 上一轮的历史由此而来
        # （真实流水线每轮提取都会调用它，见 pipeline/_extract.py）。
        state.track_semantic_changes(first, initial)
        state.save_records(first, request, initial)

        # ★ 关键：第二次运行故意换一个 config_path —— GUI 就是这样（每次运行都另存为新的
        # 时间戳 YAML）。若"首轮"按 config_path 判定，这里会被误判成首轮（回归护栏）。
        second = state.start_run("baseline", str(tmp_path / "config-round2.yaml"))
        # id=1 改标题（修改）、id=2 不变（不记）、id=3 新增
        later = _records({1: "t1-changed", 2: "t2", 3: "t3"})
        changes = state.track_semantic_changes(second, later)
        assert all(item["baseline"] is False for item in changes), "非首轮不得标 baseline"
        state.save_records(second, request, later)

        report = build_quality_report(config, state, second)

    assert report["semantic_changes_baseline"] == {}, "非首轮没有基线变化"
    assert report["semantic_changes"] == {"added": 1, "modified": 1}, report["semantic_changes"]
    assert notifiable_changes(report) == {"added": 1, "modified": 1}, (
        "真实变化必须能被「要提示」的场景看到"
    )
    # 完整事实不变：报表 / 看板读 semantic_changes 时两类变化都在
    assert report["semantic_changes"] == {"added": 1, "modified": 1}


def test_legacy_state_db_without_baseline_column_is_migrated(tmp_path: Path) -> None:
    """旧库（无 baseline 列）打开后自动补列，历史行默认 0（非基线）。"""
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE semantic_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            record_type TEXT NOT NULL,
            identity TEXT NOT NULL,
            change_type TEXT NOT NULL,
            similarity REAL NOT NULL,
            added_json TEXT NOT NULL,
            removed_json TEXT NOT NULL,
            modified_json TEXT NOT NULL,
            before_json TEXT,
            after_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO semantic_changes(run_id, source_url, record_type, identity, change_type,"
        " similarity, added_json, removed_json, modified_json, created_at)"
        " VALUES('legacy-run', ?, 'item', 'item|id:1', 'added', 0.0, '[]', '[]', '[]', '2026-01-01')",
        (_URL,),
    )
    conn.commit()
    conn.close()

    with StateStore(path) as state:
        columns = {row[1] for row in state.conn.execute("PRAGMA table_info(semantic_changes)")}
        assert "baseline" in columns, "打开旧库时应自动补 baseline 列"
        row = state.rows("SELECT baseline, change_type FROM semantic_changes")[0]

    assert int(row["baseline"]) == 0, "历史行应为非基线（默认 0）"
    assert row["change_type"] == "added", "迁移不得改动既有数据"

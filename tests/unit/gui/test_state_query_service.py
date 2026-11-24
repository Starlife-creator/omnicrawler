"""`gui/core/state_query_service.py`：不接触 Qt 的状态库查询薄适配层。

为什么要有这个文件：该模块此前是**唯一一个 0% 覆盖且仍有 25 行可执行语句**的
模块（`coverage.json` 按「<5% 且 ≥20 行」筛出）。它本身是纯函数、不碰 Qt 控件，
缺测纯粹是因为「骨架就位、尚未接线」—— 而正因为它还没接线，**接线时才第一次被
执行的那段代码**恰恰是最容易写错的部分（`load_timeline` 找不到 run 时返回 None
还是抛错？DB 打不开时是空列表还是异常？）。

所以这里钉住的是**失败语义**，而不只是 happy path：

* 库不存在 ⇒ 空列表（只读检视口径，不阻塞界面）
* 库损坏/异常 ⇒ 空列表，**不把异常抛到 UI 线程**
* `load_timeline` 找不到 run ⇒ `(None, events, stages)`，events/stages 仍如实返回
* 三个函数都**不得**因为「表里没数据」而返回 None 或抛错
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.gui.core import state_query_service as sqs
from omnicrawler.state import StateStore


def _seeded_store(db_path: Path) -> str:
    """建一个真库并写入一条 run（含事件与 checkpoint），返回 run_id。"""
    with StateStore(db_path) as state:
        run_id = state.start_run("demo-project", "configs/demo.yaml")
        state.transition_run(run_id, "succeeded", reason="done")
    return run_id


def test_load_recent_runs_returns_rows_newest_first(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    _seeded_store(db)
    rows = sqs.load_recent_runs(db)
    assert rows, "刚写入的 run 必须能被读回"
    assert rows[0]["project_name"] == "demo-project"
    assert {"run_id", "project_name", "status", "started_at", "finished_at"} <= set(rows[0])


def test_load_recent_runs_honours_limit(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    with StateStore(db) as state:
        for index in range(5):
            state.start_run(f"p{index}", "configs/demo.yaml")
    assert len(sqs.load_recent_runs(db, limit=2)) == 2


def test_load_recent_runs_missing_database_is_empty_not_an_error(tmp_path: Path) -> None:
    """库不存在时返回空列表 —— 只读检视不该让界面崩。"""
    assert sqs.load_recent_runs(tmp_path / "absent.sqlite3") == []


def test_load_recent_runs_corrupt_database_is_empty_not_an_error(tmp_path: Path) -> None:
    """库损坏时同样返回空列表：异常**不得**抛到 UI 线程。"""
    bad = tmp_path / "corrupt.sqlite3"
    bad.write_bytes(b"this is not a sqlite database")
    assert sqs.load_recent_runs(bad) == []


def test_load_timeline_returns_run_events_and_stages(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    run_id = _seeded_store(db)
    run, events, stages = sqs.load_timeline(db, run_id)
    assert run is not None and run["run_id"] == run_id
    assert events, "状态迁移事件必须被读回"
    assert {event["to_state"] for event in events} >= {"running", "succeeded"}
    assert isinstance(stages, list)


def test_load_timeline_unknown_run_returns_none_but_keeps_lists(tmp_path: Path) -> None:
    """未知 run：`run` 为 None，但另外两项仍是**列表**而不是 None。

    这条是接口形状的守卫 —— 调用方会在拿到结果后直接遍历 events/stages，
    返回 None 会让 UI 在「run 不存在」这个最常见的情况下崩。
    """
    db = tmp_path / "state.sqlite3"
    _seeded_store(db)
    run, events, stages = sqs.load_timeline(db, "no-such-run")
    assert run is None
    assert events == [] and stages == []
    assert isinstance(events, list) and isinstance(stages, list)


def test_load_timeline_missing_database_is_empty_and_creates_nothing(tmp_path: Path) -> None:
    """库不存在 ⇒ 空三元组，且**不得**顺手把库建出来。

    这条来自一次实测：`StateStore.__init__` 会建库建表，所以原先的
    `with StateStore(db_path)` 让「打开详情页看一眼」在磁盘上凭空造出一个
    状态库。对纯只读的检视入口，这是不该有的副作用 —— 尤其它发生在
    UI 线程上、且用户根本没有要采集任何东西。
    """
    missing = tmp_path / "absent.sqlite3"
    assert sqs.load_timeline(missing, "any-run") == (None, [], [])
    assert not missing.exists(), "只读查询不得创建状态库"


def test_load_run_selector_rows_projects_only_three_columns(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    _seeded_store(db)
    rows = sqs.load_run_selector_rows(db)
    assert rows and set(rows[0]) == {"run_id", "started_at", "status"}


def test_load_run_selector_rows_missing_database_is_empty(tmp_path: Path) -> None:
    assert sqs.load_run_selector_rows(tmp_path / "absent.sqlite3") == []


def test_load_run_selector_rows_honours_limit(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    with StateStore(db) as state:
        for index in range(4):
            state.start_run(f"p{index}", "configs/demo.yaml")
    assert len(sqs.load_run_selector_rows(db, limit=2)) == 2

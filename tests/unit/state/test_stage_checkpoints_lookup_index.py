"""增量抓取的观测复用查询必须走索引，不能全表扫。

为什么要有这个文件（2026-11-25 实测）：

`reuse_record_observation` 在增量抓取时**每个未变页面**都会调用一次，
查询按 `stage` + `idempotency_key` 过滤 —— **不带 run_id**：

    SELECT ... FROM stage_checkpoints c
    WHERE c.stage=record_observation AND c.idempotency_key=?
    ORDER BY c.updated_at DESC, c.rowid DESC LIMIT 1

而 `stage_checkpoints` 只有 `(run_id, stage)` 索引，过滤条件里没有 run_id
⇒ 走 sqlite_autoindex 全扫。

实测（每行一条 record_observation）：

    表中 10000 行: 1.12 ms
    表中 30000 行: 4.98 ms
    表中 60000 行: 9.10 ms

5 万页增量抓取 = 7 分钟以上，只花在这一次查询上。加索引后 0.02 ms。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.state.state_store import StateStore

_LOOKUP = (
    "SELECT c.run_id, c.payload_json FROM stage_checkpoints c "
    "JOIN runs u ON u.run_id=c.run_id LEFT JOIN run_identities i ON i.run_id=c.run_id "
    "JOIN stage_checkpoints s ON s.run_id=c.run_id AND s.stage=? AND s.idempotency_key=? "
    "WHERE c.stage=? AND c.idempotency_key=? AND c.run_id<>? "
    "AND COALESCE(i.task_key, ? || u.project_name)=? "
    "AND json_extract(s.payload_json, ?) = ? "
    "ORDER BY c.updated_at DESC, c.rowid DESC LIMIT 1"
)
_PARAMS = ("setup", "setup", "record_observation", "fp1", "r0", "legacy:", "t", "$.comparison_scope", "s")


def _store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.sqlite3")


def test_lookup_uses_dedicated_index(tmp_path: Path) -> None:
    """查询计划里 c 必须走 idx_stage_checkpoints_lookup，不能全扫。"""
    state = _store(tmp_path)
    plan = [
        str(tuple(row)[-1])
        for row in state.conn.execute("EXPLAIN QUERY PLAN " + _LOOKUP, _PARAMS).fetchall()
    ]
    joined = " | ".join(plan)
    offending = [step for step in plan if "SCAN c " in step or step == "SCAN c"]
    assert not offending, "观测复用查询仍在全扫 stage_checkpoints：" + joined
    assert any("idx_stage_checkpoints_lookup" in step for step in plan), joined


def test_index_covers_order_by_column(tmp_path: Path) -> None:
    """索引必须含 `updated_at` —— 否则 ORDER BY 还要临时 B 树。"""
    state = _store(tmp_path)
    row = state.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
        ("index", "idx_stage_checkpoints_lookup"),
    ).fetchone()
    assert row is not None and row[0], "索引没有创建"
    assert "updated_at" in row[0], row[0]


def test_index_survives_reopen(tmp_path: Path) -> None:
    """重开数据库后索引仍在（schema 是 IF NOT EXISTS，幂等）。"""
    path = tmp_path / "state.sqlite3"
    StateStore(path).close()
    state = StateStore(path)
    names = {str(tuple(r)[1]) for r in state.conn.execute("PRAGMA index_list(stage_checkpoints)")}
    assert "idx_stage_checkpoints_lookup" in names, names

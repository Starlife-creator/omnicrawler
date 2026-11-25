"""复核决策的版本计数查询必须走索引，不能全表扫。

为什么要有这个文件（2026-11-25 实测）：

`review_persistence.save_decision` 每次保存决策前要算「这条记录已有几版」：

    SELECT COUNT(*) FROM audit_events
    WHERE action=review_decision AND json_extract(details_json, record_id)=?

`json_extract` 在普通索引里无法命中，`action` 上也没有索引 ⇒ 全表扫。

实测（每 100 行含 1 条 review_decision）：

    audit_events  10000 行: 0.32 ms
    audit_events  20000 行: 0.69 ms
    audit_events  25000 行: 2.02 ms    <- 超线性（json_extract 要逐行跑）

加部分表达式索引后 0.006 ms（快 300+ 倍）。

用部分索引而不是全表表达式索引：audit_events 绝大多数是别的 action，
全表建会让写入变慢、体积膨胀，而查询只关心这一类。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.state.state_store import StateStore

_COUNT = (
    "SELECT COUNT(*) AS n FROM audit_events "
    "WHERE action=? AND json_extract(details_json, '$.record_id')=?"
)


def _store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.sqlite3")


def test_decision_count_uses_index(tmp_path: Path) -> None:
    """查询计划必须是 SEARCH ... USING INDEX，不能 SCAN audit_events。"""
    state = _store(tmp_path)
    plan = [
        str(tuple(row)[-1])
        for row in state.conn.execute("EXPLAIN QUERY PLAN " + _COUNT, ("review_decision", "r1")).fetchall()
    ]
    joined = " | ".join(plan)
    assert not any("SCAN audit_events" in step for step in plan), (
        "复核决策计数仍在全表扫：" + joined
    )


def test_partial_index_is_declared(tmp_path: Path) -> None:
    """部分索引必须存在且带 WHERE 谓词（不能退化成全表表达式索引）。"""
    state = _store(tmp_path)
    row = state.conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_audit_review_record'"
    ).fetchone()
    assert row is not None and row[0], "部分索引没有创建"
    assert "review_decision" in row[0], row[0]


def test_index_survives_reopen(tmp_path: Path) -> None:
    """重开数据库后索引仍在（schema 是 IF NOT EXISTS，幂等）。"""
    path = tmp_path / "state.sqlite3"
    StateStore(path).close()
    state = StateStore(path)
    names = {str(tuple(r)[1]) for r in state.conn.execute("PRAGMA index_list(audit_events)")}
    assert "idx_audit_review_record" in names, names

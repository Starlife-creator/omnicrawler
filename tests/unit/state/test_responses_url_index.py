"""`save_response` 的「上次内容哈希」查询必须走索引，不能全表扫。

为什么要有这个文件（2026-11-25 实测）：

`save_response` 每次插入前都要问一句「这个 URL 上一次的内容哈希是什么」：

    SELECT content_sha256, ... FROM responses
    WHERE final_url=? OR url=? ORDER BY id DESC LIMIT 1

而 `responses` 表**原先只有 run_id 一个索引** —— 这条查询走 `SCAN responses`。

实测随表增长的劣化（每批 1 万条）：

    表中  10000 行:  1.2 ms/条
    表中  20000 行:  5.2 ms/条
    表中  30000 行:  8.4 ms/条
    表中  50000 行: 13.2 ms/条

抓 5 万页时光这一步就要多花 10 分钟以上，且随页数**持续变差**。
加索引后 0.32 ms/条 且不随表增长（快 41 倍）。

本文件用**查询计划**做判据（确定性），不用计时（随机器波动）。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.state.state_store import StateStore

_LOOKUP = (
    "SELECT content_sha256, content_type, size_bytes, etag, last_modified "
    "FROM responses WHERE final_url=? OR url=? ORDER BY id DESC LIMIT 1"
)


def _store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "state.sqlite3")


def test_lookup_uses_index_not_table_scan(tmp_path: Path) -> None:
    """查询计划里必须是 SEARCH ... USING INDEX，不能是 SCAN responses。"""
    state = _store(tmp_path)
    plan = [
        str(tuple(row)[-1])
        for row in state.conn.execute("EXPLAIN QUERY PLAN " + _LOOKUP, ("a", "b")).fetchall()
    ]
    joined = " | ".join(plan)
    assert not any("SCAN responses" in step for step in plan), (
        f"lookup 仍在全表扫：{joined}. 抓 5 万页时会额外多花 10 分钟以上"
    )
    assert any("USING INDEX idx_responses_final_url" in step for step in plan), joined
    assert any("USING INDEX idx_responses_url" in step for step in plan), joined


def test_both_url_columns_are_indexed(tmp_path: Path) -> None:
    """`final_url` 与 `url` **各建一个**索引 —— 查询是 OR，两条边都要走索引。

    只建一个的话 SQLite 的 MULTI-INDEX OR 只能覆盖一条边，另一条仍是扫描。
    """
    state = _store(tmp_path)
    names = {str(tuple(row)[1]) for row in state.conn.execute("PRAGMA index_list(responses)")}
    assert "idx_responses_final_url" in names, names
    assert "idx_responses_url" in names, names


def test_indexes_survive_reopen(tmp_path: Path) -> None:
    """重开数据库后索引仍在（schema 是 CREATE INDEX IF NOT EXISTS，幂等）。"""
    path = tmp_path / "state.sqlite3"
    StateStore(path).close()
    state = StateStore(path)
    names = {str(tuple(row)[1]) for row in state.conn.execute("PRAGMA index_list(responses)")}
    assert "idx_responses_final_url" in names
    assert "idx_responses_url" in names

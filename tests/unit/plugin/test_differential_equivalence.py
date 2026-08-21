"""Phase 2a F2：差分等价测试（代理不改语义的核心保证）。

同一逻辑操作分别以「宿主直连」与「subprocess 能力代理」两种方式运行，
断言结果逐字段一致——证明能力代理不改变插件语义。另含 session 模式差分：
session 内多次调用的结果彼此一致（F2 session 追加用例）。

注：能力代理必须经 broker + drive_loop 应答 capability 请求；一次性
IsolatedPluginRunner 不承载 broker，无法运行需要能力回调的插件逻辑
（这是 C3 架构的既定边界，非缺陷）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.plugins.plugin_broker import CapabilityBroker, drive_loop
from omnicrawler.plugins.plugin_sandbox import PluginSubprocessSession
from omnicrawler.state.state_store import StateStore

pytestmark = pytest.mark.plugin_contract


@pytest.fixture()
def state(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.sqlite3")
    run_id = store.start_run("diff-project", "<test>")
    request = CrawlRequest("https://example.com/seed", kind="plugin")
    records = [
        ExtractedRecord(
            source_url="https://example.com/1",
            record_type="news",
            data={"title": f"item-{i}", "rank": i},
            evidence={"plugin": "diff"},
        )
        for i in range(5)
    ]
    store.save_records(run_id, request, records)
    store._diff_run_id = run_id  # 供测试读取真实 run_id
    return store


@pytest.fixture()
def read_plugin_dir(tmp_path: Path) -> Path:
    plugin = tmp_path / "read_plugin"
    plugin.mkdir()
    (plugin / "diff_plugin.py").write_text(
        "import omnicrawler_sdk\n"
        "def handle(operation, payload):\n"
        "    if operation == 'records':\n"
        "        return omnicrawler_sdk.call('records.read', payload)\n"
        "    return {}\n",
        encoding="utf-8",
    )
    return plugin


def _direct_records_read(store: StateStore, run_id: str, limit: int) -> dict:
    """宿主侧直连实现（与 broker._cap_records_read 同 SQL 语义）。"""
    rows = store.rows(
        "SELECT record_id, source_url, data_json FROM records WHERE run_id=?"
        " ORDER BY rowid DESC LIMIT ?",
        (run_id, limit),
    )
    records = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except (json.JSONDecodeError, KeyError):
            data = {}
        records.append(
            {"record_id": row["record_id"], "source_url": row["source_url"], "data": data}
        )
    return {"records": records, "count": len(records)}


def _make_broker(state: StateStore) -> CapabilityBroker:
    return CapabilityBroker(
        permissions={"records:read"},
        system_info={"version": "diff"},
        state_store=state,
        run_id=state._diff_run_id,
    )


def test_records_read_differential(state: StateStore, read_plugin_dir: Path) -> None:
    """records.read 经代理 vs 直连：逐字段一致（F2 核心）。"""
    expected = _direct_records_read(state, state._diff_run_id, 10)
    broker = _make_broker(state)

    with PluginSubprocessSession(
        read_plugin_dir, "diff_plugin", timeout_seconds=15
    ) as session:
        session.start()
        proxied = drive_loop(session, broker, "records", {"limit": 10}, timeout_seconds=0)

    assert proxied == expected, "代理结果与直连不一致"
    assert proxied["count"] == 5
    assert [r["data"]["title"] for r in proxied["records"]] == [
        f"item-{i}" for i in range(4, -1, -1)
    ]


def test_session_repeat_calls_consistent(state: StateStore, read_plugin_dir: Path) -> None:
    """session 内多次调用同一操作：结果彼此一致（session 差分）。"""
    broker = _make_broker(state)
    with PluginSubprocessSession(
        read_plugin_dir, "diff_plugin", timeout_seconds=15
    ) as session:
        session.start()
        results = [
            drive_loop(session, broker, "records", {"limit": 3}, timeout_seconds=0)
            for _ in range(3)
        ]
    assert results[0] == results[1] == results[2]


def test_session_matches_direct_across_restarts(
    state: StateStore, read_plugin_dir: Path
) -> None:
    """跨会话重启结果仍与直连一致（代理无隐藏状态）。"""
    expected = _direct_records_read(state, state._diff_run_id, 5)

    for _ in range(2):  # 两次独立会话
        broker = _make_broker(state)
        with PluginSubprocessSession(
            read_plugin_dir, "diff_plugin", timeout_seconds=15
        ) as session:
            session.start()
            proxied = drive_loop(session, broker, "records", {"limit": 5}, timeout_seconds=0)
        assert proxied == expected


def test_records_read_with_source_url_filter(
    state: StateStore, read_plugin_dir: Path
) -> None:
    """source_url 过滤参数经代理后语义一致。"""
    expected = {
        "records": [
            {
                "record_id": r["record_id"],
                "source_url": r["source_url"],
                "data": r["data"],
            }
            for r in _direct_records_read(state, state._diff_run_id, 100)["records"]
            if r["source_url"] == "https://example.com/1"
        ],
    }
    expected["count"] = len(expected["records"])
    broker = _make_broker(state)

    with PluginSubprocessSession(
        read_plugin_dir, "diff_plugin", timeout_seconds=15
    ) as session:
        session.start()
        proxied = drive_loop(
            session, broker, "records",
            {"limit": 100, "source_url": "https://example.com/1"},
            timeout_seconds=0,
        )
    assert proxied == expected

"""Phase 2b H4：共现事件 SIEM 导出契约测试（plugins audit --export-egress）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_audit import _EGRESS_EXPORT_FIELDS, export_egress_cooccurrence
from omnicrawler.state.state_store import StateStore

pytestmark = pytest.mark.plugin_contract


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    state = StateStore(tmp_path / "state.sqlite3")
    run_id = state.start_run("egress-project", "<test>")
    # 写入两条共现审计事件（模拟 broker audit_hook 落库）
    for plugin_id, read_before in (("demo_a", 1), ("demo_b", 3)):
        state.add_audit_event(
            "plugin.egress_cooccurrence",
            run_id=run_id,
            actor="plugin:subprocess",
            details={
                "plugin_id": plugin_id,
                "decision": "cooccurrence_risk",
                "records_read_before": read_before,
            },
        )
    state._diff_run_id = run_id
    return state


def test_export_writes_jsonl_with_whitelisted_fields(store: StateStore, tmp_path: Path) -> None:
    out = tmp_path / "egress.jsonl"
    count = export_egress_cooccurrence(store, out)
    assert count == 2
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    # 只导出白名单字段（零插件明细外泄，H7 语义）
    assert set(first) == set(_EGRESS_EXPORT_FIELDS)
    assert first["plugin_id"] in ("demo_a", "demo_b")
    assert first["decision"] == "cooccurrence_risk"
    assert first["operation"] == "records.read->network.fetch"


def test_export_empty_when_no_cooccurrence(tmp_path: Path) -> None:
    # 全新 store，仅写入无关审计事件 → 共现导出为空
    fresh = StateStore(tmp_path / "empty_state.sqlite3")
    run_id = fresh.start_run("egress-empty", "<test>")
    fresh.add_audit_event("plugin.subprocess.call", run_id=run_id, actor="system")
    out = tmp_path / "empty.jsonl"
    count = export_egress_cooccurrence(fresh, out)
    assert count == 0
    assert out.read_text(encoding="utf-8").strip() == ""


def test_export_field_whitelist_complete() -> None:
    """导出字段清单对齐 C6 schema（时间戳/插件/操作/域名/判定/会话号）。"""
    assert set(_EGRESS_EXPORT_FIELDS) == {
        "timestamp_utc", "plugin_id", "plugin_version", "operation", "domain",
        "decision", "session_id",
    }

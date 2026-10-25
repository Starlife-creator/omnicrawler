"""A-P2-2：审计哈希链追加、重放校验、严格模式 fail-closed 与 SQLite 联动回归测试。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from omnicrawler.security.audit_chain import AuditChain, AuditChainBrokenError, GENESIS_HASH
from omnicrawler.state import StateStore


def test_chain_appends_linked_records_with_monotonic_seq(tmp_path: Path) -> None:
    chain = AuditChain(tmp_path / "logs" / "chain.jsonl")
    first = chain.append({"action": "login", "subject": "u1"}, action="auth.login")
    second = chain.append({"action": "fetch", "url": "https://example.com"}, action="net.fetch")
    third = chain.append({"action": "logout", "subject": "u1"}, action="auth.logout")

    assert first["seq"] == 1 and second["seq"] == 2 and third["seq"] == 3
    assert first["prev_hash"] == GENESIS_HASH
    assert second["prev_hash"] == first["record_hash"]
    assert third["prev_hash"] == second["record_hash"]
    report = chain.verify()
    assert report["ok"] is True
    assert report["verified"] == 3
    assert report["broken"] == []


def test_chain_bootstrap_continues_seq_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "chain.jsonl"
    AuditChain(path).append({"n": 1})
    AuditChain(path).append({"n": 2})
    reopened = AuditChain(path)
    third = reopened.append({"n": 3})
    assert third["seq"] == 3
    assert reopened.verify()["ok"] is True


def test_verify_detects_tampered_payload(tmp_path: Path) -> None:
    path = tmp_path / "chain.jsonl"
    chain = AuditChain(path)
    chain.append({"amount": 100}, action="payment")
    chain.append({"amount": 200}, action="payment")

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["event"]["amount"] = 9999  # 篡改载荷但不重算哈希
    lines[0] = json.dumps(record, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = chain.verify()
    assert report["ok"] is False
    assert report["verified"] == 2
    assert report["broken"][0]["seq"] == 1
    assert any("record_hash" in problem for problem in report["broken"][0]["problems"])


def test_strict_mode_fails_closed_on_tamper(tmp_path: Path) -> None:
    path = tmp_path / "chain.jsonl"
    chain = AuditChain(path, strict=True)
    chain.append({"n": 1})
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["event"]["n"] = 2
    lines[0] = json.dumps(record, ensure_ascii=False, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(AuditChainBrokenError):
        chain.verify()


def test_chain_persists_seq_and_hashes_into_sqlite(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temp:
        state = StateStore(Path(temp) / "state.sqlite3")
        try:
            chain = AuditChain(tmp_path / "chain.jsonl", state=state)
            chain.append({"action": "grant", "role": "editor"}, action="auth.grant", run_id="run-1")
            rows = state.rows(
                "SELECT chain_seq, prev_hash, record_hash, action FROM audit_events "
                "WHERE action='auth.grant' ORDER BY id"
            )
            assert len(rows) == 1
            assert rows[0]["chain_seq"] == 1
            assert rows[0]["prev_hash"] == GENESIS_HASH
            assert rows[0]["record_hash"]
            assert rows[0]["action"] == "auth.grant"
        finally:
            state.close()

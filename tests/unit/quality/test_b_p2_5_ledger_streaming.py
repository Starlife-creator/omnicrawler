"""B-P2-5：证据台账按行流式读取；尾部单行取哈希；审计链完整性与篡改检测保持不变。"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.quality.evidence_ledger import EvidenceLedger, FieldLineage


def _lineage(record_id: str, field: str, value: str) -> FieldLineage:
    return FieldLineage(
        record_id=record_id,
        field=field,
        value=value,
        origin="test",
        source_url="https://example.com",
        response_id="resp-1",
        page=1,
        rule="css:h1",
        model="none",
        observed_at="2026-01-01T00:00:00Z",
        confirmed_by="test",
    )


def test_field_history_filters_without_loading_whole_file(tmp_path: Path) -> None:
    ledger = EvidenceLedger(tmp_path / "ledger")
    for index in range(30):
        ledger.append_lineage(_lineage(f"rec-{index % 3}", "title", f"v{index}"))
    history = ledger.field_history("rec-0", "title")
    assert len(history) == 10
    assert all(item["record_id"] == "rec-0" and item["field"] == "title" for item in history)


def test_replay_payload_roundtrip_and_verify_chain(tmp_path: Path) -> None:
    ledger = EvidenceLedger(tmp_path / "ledger")
    node = ledger.append_node(
        "record", b'{"title": "hello"}',
        stage="parse", version="1", metadata={"source": "test"},
    )
    payload = ledger.replay_payload(node.node_id)
    assert payload == b'{"title": "hello"}'

    ok, count = ledger.verify()
    assert ok is True and count >= 1
    # 尾部单行哈希与最后一条审计记录一致
    last = json.loads(ledger.audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert ledger._last_hash() == last["entry_hash"]


def test_verify_detects_tampered_chain(tmp_path: Path) -> None:
    ledger = EvidenceLedger(tmp_path / "ledger")
    ledger.append_audit("event-a", {"n": 1})
    ledger.append_audit("event-b", {"n": 2})
    ok, _ = ledger.verify()
    assert ok is True

    lines = ledger.audit_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[-1])
    tampered["payload"] = {"n": 999}
    lines[-1] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
    ledger.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, count = ledger.verify()
    assert ok is False and count == 1

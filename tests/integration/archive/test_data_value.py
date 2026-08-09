from __future__ import annotations

import json

import pytest

from omnicrawl.quality.evidence_ledger import EvidenceLedger, FieldLineage
from omnicrawl.quality.schema_registry import (
    DatasetContract,
    FieldContract,
    SchemaRegistry,
    analyse_contract_change,
)
from omnicrawl.quality.temporal_facts import (
    EntityRegistry,
    TemporalFact,
    infer_business_event,
    stable_entity_id,
)
from omnicrawl.security.data_governance import (
    deletion_manifest,
    detect_sensitive_fields,
    export_privacy_summary,
)


def test_evidence_graph_hash_chain_lineage_manifest_and_replay(tmp_path):
    ledger = EvidenceLedger(tmp_path / "ledger")
    raw = ledger.append_node("raw_response", b"original bytes", stage="fetch", version="1", metadata={"url": "https://example.com"})
    parsed = ledger.append_node("parsed_record", b'{"amount":100}', stage="parse", version="2", metadata={}, parents=(raw.node_id,))
    ledger.append_lineage(FieldLineage(
        "r1", "amount", 100, "rule", "https://example.com", raw.node_id, 2, "jsonpath:$.amount", "", "2026-07-22T00:00:00Z", "reviewer-1"
    ))
    manifest = ledger.manifest(config_hash="c", ir_hash="i", plan_hash="p", software_version="1.7.0", components={"ocr": "3"})
    assert manifest.is_file()
    assert ledger.replay_payload(parsed.node_id) == b'{"amount":100}'
    assert ledger.field_history("r1", "amount")[0]["confirmed_by"] == "reviewer-1"
    ok, count = ledger.verify()
    assert ok and count == 4

    lines = ledger.audit_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["node_id"] = "tampered"
    lines[1] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
    ledger.audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ledger.verify()[0] is False


def test_schema_registry_reports_breaking_and_migration_impacts():
    v1 = DatasetContract("notices", "1.0", (
        FieldContract("id", "string", "业务编号", required=True, unique=True),
        FieldContract("amount", "number", "金额"),
    ), consumers=("excel", "warehouse"))
    v2 = DatasetContract("notices", "2.0", (
        FieldContract("id", "string", "业务编号", required=True, unique=True),
        FieldContract("amount", "string", "格式化金额"),
        FieldContract("status", "string", "状态", required=True),
    ), consumers=("excel", "api"))
    impact = analyse_contract_change(v1, v2)
    assert impact.compatibility == "breaking"
    assert impact.type_changes == ("amount",)
    assert impact.historical_reprocess_required
    assert impact.affected_consumers == ("api", "excel", "warehouse")
    registry = SchemaRegistry()
    registry.register(v1)
    registry.register(v1)
    with pytest.raises(ValueError):
        registry.register(DatasetContract("notices", "1.0", (FieldContract("x", "string", "x"),)))


def test_governance_detects_pii_and_only_plans_verifiable_deletion(tmp_path):
    records = [{"email": "person@example.com", "phone": "13800138000", "public": "news"}]
    assert {finding.field for finding in detect_sensitive_fields(records[0])} == {"email", "phone"}
    summary = export_privacy_summary(records)
    assert summary["approval_recommended"] is True
    workspace = tmp_path / "workspace"
    raw = workspace / "raw" / "evidence.bin"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"evidence")
    manifest = deletion_manifest([raw], workspace, categories={str(raw.resolve()): "raw_evidence"})
    assert manifest["requires_confirmation"] is True
    assert manifest["items"][0]["category"] == "raw_evidence"
    assert raw.exists(), "planning must never silently delete evidence"
    with pytest.raises(ValueError):
        deletion_manifest([tmp_path / "outside"], workspace)


def test_temporal_entities_aliases_and_business_events():
    entity = stable_entity_id("notice", "ABC-123")
    before = TemporalFact(entity, "amount", 100, "2026-01-01", "2026-01-02", "https://a", "e1")
    after = TemporalFact(entity, "amount", 120, "2026-02-01", "2026-02-02", "https://a", "e2")
    event = infer_business_event(before, after)
    assert event.event_type == "amount_changed"
    assert event.before == 100 and event.after == 120
    registry = EntityRegistry()
    registry.merge("notice:alias", entity)
    assert registry.resolve("notice:alias") == entity
    registry.split("notice:alias")
    assert registry.resolve("notice:alias") == "notice:alias"

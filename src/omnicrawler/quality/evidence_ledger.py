"""Append-only evidence graph and tamper-evident audit ledger."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.utils import atomic_write, utcnow


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    node_id: str
    kind: str
    sha256: str
    created_at: str
    stage: str
    version: str
    metadata: dict[str, Any]
    parents: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldLineage:
    record_id: str
    field: str
    value: Any
    origin: str
    source_url: str
    response_id: str
    page: int | None
    rule: str
    model: str
    observed_at: str
    confirmed_by: str


class EvidenceLedger:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.nodes_path = root / "evidence-nodes.jsonl"
        self.audit_path = root / "audit-chain.jsonl"
        self.lineage_path = root / "field-lineage.jsonl"
        self.objects = root / "objects"
        root.mkdir(parents=True, exist_ok=True)
        self.objects.mkdir(exist_ok=True)

    def append_node(self, kind: str, payload: bytes, *, stage: str, version: str, metadata: dict[str, Any], parents: Iterable[str] = ()) -> EvidenceNode:
        digest = hashlib.sha256(payload).hexdigest()
        node_id = hashlib.sha256(f"{kind}:{digest}:{stage}:{version}".encode()).hexdigest()[:32]
        object_path = self.objects / digest[:2] / digest[2:]
        object_path.parent.mkdir(parents=True, exist_ok=True)
        if not object_path.exists():
            atomic_write(object_path, payload)
        node = EvidenceNode(node_id, kind, digest, utcnow(), stage, version, metadata, tuple(parents))
        self._append_json(self.nodes_path, asdict(node))
        self.append_audit("evidence_added", {"node_id": node_id, "sha256": digest, "parents": list(node.parents)})
        return node

    def append_lineage(self, lineage: FieldLineage) -> None:
        self._append_json(self.lineage_path, asdict(lineage))
        self.append_audit("field_lineage", {"record_id": lineage.record_id, "field": lineage.field, "response_id": lineage.response_id})

    def field_history(self, record_id: str, field: str) -> tuple[dict[str, Any], ...]:
        # B-P2-5：台账按行流式扫描，不再整份 JSONL 读入内存。
        if not self.lineage_path.is_file():
            return ()
        matches: list[dict[str, Any]] = []
        with self.lineage_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("record_id") == record_id and item.get("field") == field:
                    matches.append(item)
        return tuple(matches)

    def replay_payload(self, node_id: str) -> bytes:
        """Return the immutable stage payload selected by node id for deterministic replay."""
        # B-P2-5：节点索引按行流式扫描，不再整份 nodes JSONL 入内存。
        if not self.nodes_path.is_file():
            raise KeyError(node_id)
        with self.nodes_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("node_id") == node_id:
                    digest = str(item["sha256"])
                    payload = (self.objects / digest[:2] / digest[2:]).read_bytes()
                    if hashlib.sha256(payload).hexdigest() != digest:
                        raise ValueError("证据对象哈希不匹配")
                    return payload
        raise KeyError(node_id)

    def append_audit(self, event: str, payload: dict[str, Any]) -> str:
        previous = self._last_hash()
        entry = {"timestamp": utcnow(), "event": event, "payload": payload, "previous_hash": previous}
        entry_hash = hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self._append_json(self.audit_path, {**entry, "entry_hash": entry_hash})
        return entry_hash

    def verify(self) -> tuple[bool, int]:
        previous = "0" * 64
        count = 0
        if not self.audit_path.is_file():
            return True, 0
        # B-P2-5：审计链按行流式校验，不再整份读入内存。
        with self.audit_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                claimed = item.pop("entry_hash")
                actual = hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if claimed != actual or item["previous_hash"] != previous:
                    return False, count
                previous = claimed
                count += 1
        return True, count

    def manifest(self, *, config_hash: str, ir_hash: str, plan_hash: str, software_version: str, components: dict[str, str]) -> Path:
        path = self.root / "run-manifest.json"
        payload = {
            "config_hash": config_hash, "ir_hash": ir_hash, "plan_hash": plan_hash,
            "software_version": software_version, "components": components, "created_at": utcnow(),
        }
        atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode())
        self.append_audit("run_manifest", {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        return path

    def _last_hash(self) -> str:
        # B-P2-5：尾部单行取哈希，仅流式扫描到最后一条非空行，不再整份重读审计链（O(n²) 消除）。
        if not self.audit_path.is_file():
            return "0" * 64
        last_line: str | None = None
        with self.audit_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line.strip()
        if last_line is None:
            return "0" * 64
        return str(json.loads(last_line)["entry_hash"])

    @staticmethod
    def _append_json(path: Path, value: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n")

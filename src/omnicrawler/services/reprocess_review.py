"""Explicit record mapping and transactional decisions for protected re-extraction."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..core.utils import json_text, utcnow
from ..state import StateStore


class ReprocessReview:
    def __init__(self, state: StateStore) -> None:
        self.state = state

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self.state._lock:
            if self.state.conn.in_transaction:
                raise ValueError("Review requires its own transaction")
            with self.state.conn:
                # Lock other database connections before checking the displayed snapshot.
                self.state.conn.execute("BEGIN IMMEDIATE")
                yield

    def load(self, record_id: str) -> dict[str, Any]:
        with self.state._lock:
            row = self.state.conn.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
            if row is None:
                raise KeyError("Record no longer exists")
            record = dict(row)
            checkpoint = self.state.checkpoint(record["run_id"], "reprocess_candidate", record["request_fingerprint"])
            if checkpoint is None or record_id not in checkpoint["payload"].get("preserved_record_ids", []):
                raise ValueError("No re-extraction candidate for this record")
            payload = checkpoint["payload"]
            if record_id in payload.get("decisions", {}):
                raise ValueError("This record already has a review decision")
            token = hashlib.sha256(json_text([record["data_json"], record["evidence_json"], payload]).encode()).hexdigest()
            return {"record_id": record_id, "run_id": record["run_id"], "request_fingerprint": record["request_fingerprint"],
                    "data": json.loads(record["data_json"]), "evidence": json.loads(record["evidence_json"]),
                    "source_url": record["source_url"], "record_type": record["record_type"],
                    "candidate": payload, "token": token}

    def resolve(self, record_id: str, token: str, *, candidate_index: int | None,
                actor: str = "local-user", reason: str) -> dict[str, Any]:
        """None rejects; an explicit index accepts a whole record, including absent fields."""
        if not actor.strip() or not reason.strip():
            raise ValueError("Review actor and reason are required")
        with self._transaction():
            current = self.load(record_id)
            if current["token"] != token:
                raise ValueError("Record or candidates changed; reload before reviewing")
            payload = current["candidate"]
            decisions = payload.setdefault("decisions", {})
            evidence = copy.deepcopy(current["evidence"])
            data = current["data"]
            source_url, record_type = current["source_url"], current["record_type"]
            if candidate_index is not None:
                if type(candidate_index) is not int or not 0 <= candidate_index < len(payload["records"]):
                    raise ValueError("Invalid candidate index")
                if any(decision.get("candidate_index") == candidate_index for decision in decisions.values()):
                    raise ValueError("Candidate already mapped to another record")
                selected = payload["records"][candidate_index]
                if not isinstance(selected.get("data"), dict) or not isinstance(selected.get("evidence"), dict):
                    raise ValueError("Invalid candidate record")
                data = copy.deepcopy(selected["data"])
                evidence = copy.deepcopy(selected["evidence"])
                source_url, record_type = selected["source_url"], selected["record_type"]
                # Keep the manual provenance even when the user explicitly replaces values.
                evidence["_review"] = copy.deepcopy(current["evidence"].get("_review", {}))
            else:
                evidence.setdefault("_quality", {})["review_required"] = payload.get("previous_review_required", {}).get(record_id, True)
            decision = {"decision": "accepted" if candidate_index is not None else "rejected",
                        "candidate_index": candidate_index, "actor": actor, "reason": reason,
                        "created_at": utcnow(), "candidate_id": payload.get("candidate_id"),
                        "content_sha256": payload["content_sha256"]}
            review = evidence.setdefault("_review", {})
            review.pop("reprocess_candidate", None)
            review.setdefault("reprocess_decisions", []).append(decision)
            decisions[record_id] = decision
            if all(identity in decisions for identity in payload["preserved_record_ids"]):
                payload["status"] = "reviewed"
                payload["unmapped_candidate_indexes"] = [index for index in range(len(payload["records"]))
                    if not any(item.get("candidate_index") == index for item in decisions.values())]
            now = utcnow()
            self.state.conn.execute(
                "UPDATE records SET data_json=?, evidence_json=?, source_url=?, record_type=? WHERE record_id=?",
                (json_text(data), json_text(evidence), source_url, record_type, record_id),
            )
            if candidate_index is not None:
                self.state.conn.execute(
                    "INSERT INTO record_edits(record_id,field_name,old_value_json,new_value_json,actor,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                    (record_id, "_reprocess_record", json_text(current["data"]), json_text(data), actor, reason, now),
                )
            self.state.conn.execute(
                "UPDATE stage_checkpoints SET payload_json=?, updated_at=? WHERE run_id=? AND stage='reprocess_candidate' AND idempotency_key=?",
                (json_text(payload), now, current["run_id"], current["request_fingerprint"]),
            )
            self.state.conn.execute(
                "INSERT INTO audit_events(run_id,action,actor,details_json,created_at) VALUES(?,?,?,?,?)",
                (current["run_id"], "reprocess_review", actor,
                 json_text({"record_id": record_id, "before": current["data"], "after": data, **decision}), now),
            )
            return {"record_id": record_id, "data": data, "evidence": evidence, "source_url": source_url}

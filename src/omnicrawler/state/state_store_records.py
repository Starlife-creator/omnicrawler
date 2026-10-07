"""StateStore 的「响应与记录」域 —— 从 state_store.py 抽出的 Mixin（P1-3 第二批）。

覆盖响应落盘、记录批量写入与语义变更追踪：
``save_response`` / ``save_records`` / ``_preload_versions`` /
``track_semantic_changes`` / ``reset_record_stage``。

宿主共享方法（``_require_run_id`` / ``add_audit_event``）经 TYPE_CHECKING 声明；
``rows`` 作为通用查询入口保留在宿主。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..core.models import CrawlRequest, ExtractedRecord, FetchResult
from ..core.record_storage_identity import storage_identity
from ..core.utils import json_text, utcnow
from ..quality.semantic_changes import (
    compare_record_data,
    entity_checkpoint_key,
    record_identity,
    semantic_hash,
)
from .notification_queue import enqueue_event
from .record_notification_policy import evaluate_policy

if TYPE_CHECKING:
    import sqlite3


class RecordsMixin:
    """响应 / 记录域。"""

    # ---- 宿主契约 ----
    conn: sqlite3.Connection
    _lock: Any

    if TYPE_CHECKING:
        @staticmethod
        def _require_run_id(run_id: str | None) -> str | None: ...
        def add_audit_event(self, *args: Any, **kwargs: Any) -> Any: ...
        def checkpoint(self, run_id: str, stage: str, idempotency_key: str) -> dict[str, Any] | None: ...
        def save_checkpoint(
            self, run_id: str, stage: str, idempotency_key: str, payload: dict[str, Any],
            *, status: str = "succeeded",
        ) -> None: ...

    def save_record_observation(
        self, run_id: str, result: FetchResult, records: list[ExtractedRecord],
    ) -> None:
        """Keep a per-request snapshot independently of incremental delivery."""
        self.save_checkpoint(run_id, "record_observation", result.request.fingerprint, {
            "content_sha256": result.content_hash,
            "final_url": result.final_url,
            "records": [
                {"source_url": r.source_url, "record_type": r.record_type, "data": r.data}
                for r in records
            ],
        })

    def reuse_record_observation(
        self, run_id: str, result: FetchResult, *, notification: dict[str, Any] | None = None,
        identity_fields: tuple[str, ...] = (), ignored_fields: set[str] | None = None,
    ) -> bool:
        """Reuse only a matching observed response under the same extraction scope."""
        setup = (self.checkpoint(run_id, "setup", "setup") or {}).get("payload", {})
        scope = setup.get("comparison_scope")
        if not scope:
            return False
        with self._lock:
            response = self.conn.execute(
                "SELECT content_sha256 FROM responses WHERE run_id=? AND request_fingerprint=? "
                "ORDER BY id DESC LIMIT 1", (run_id, result.request.fingerprint),
            ).fetchone()
            task_key, _scope = self._observation_context(run_id)
            row = self.conn.execute(
                "SELECT c.run_id, c.payload_json FROM stage_checkpoints c "
                "JOIN runs u ON u.run_id=c.run_id LEFT JOIN run_identities i ON i.run_id=c.run_id "
                "JOIN stage_checkpoints s ON s.run_id=c.run_id AND s.stage='setup' AND s.idempotency_key='setup' "
                "WHERE c.stage='record_observation' AND c.idempotency_key=? AND c.run_id<>? "
                "AND COALESCE(i.task_key, 'legacy:' || u.project_name)=? "
                "AND json_extract(s.payload_json,'$.comparison_scope')=? "
                "ORDER BY c.updated_at DESC, c.rowid DESC LIMIT 1",
                (result.request.fingerprint, run_id, task_key, scope),
            ).fetchone()
        if row is None:
            return False
        previous_setup = (self.checkpoint(row["run_id"], "setup", "setup") or {}).get("payload", {})
        payload = json.loads(row["payload_json"])
        if (previous_setup.get("comparison_scope") != scope
                or payload.get("content_sha256") != (response["content_sha256"] if response else result.content_hash)
                or payload.get("final_url") != result.final_url):
            return False
        if notification:
            self.track_semantic_changes(
                run_id, [ExtractedRecord(item["source_url"], item["record_type"], item["data"])
                         for item in payload.get("records", [])],
                identity_fields=identity_fields, ignored_fields=ignored_fields, notification=notification,
            )
        self.save_checkpoint(run_id, "record_observation", result.request.fingerprint, payload)
        return True

    def save_response(self, run_id: str, result: FetchResult, raw_path: str | None) -> bool:
        self._require_run_id(run_id)
        now = utcnow()
        with self._lock, self.conn:
            latest = self.conn.execute(
                "SELECT content_sha256, content_type, size_bytes, etag, last_modified FROM responses "
                "WHERE final_url=? OR url=? ORDER BY id DESC LIMIT 1",
                (result.final_url, result.request.url),
            ).fetchone()
            not_modified = result.status == 304 or bool(result.meta.get("not_modified"))
            digest = latest["content_sha256"] if not_modified and latest else result.content_hash
            content_type = latest["content_type"] if not_modified and latest else result.content_type
            size_bytes = int(latest["size_bytes"]) if not_modified and latest else len(result.body)
            etag = result.headers.get("etag") or (latest["etag"] if not_modified and latest else None)
            last_modified = result.headers.get("last-modified") or (
                latest["last_modified"] if not_modified and latest else None
            )
            changed = False if not_modified else latest is None or latest["content_sha256"] != digest
            self.conn.execute(
                """
                INSERT INTO responses(
                    run_id, request_fingerprint, url, final_url, status_code, content_type,
                    size_bytes, content_sha256, raw_path, etag, last_modified,
                    changed, elapsed_seconds, fetched_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id, result.request.fingerprint, result.request.url, result.final_url,
                    result.status, content_type, size_bytes, digest,
                    raw_path, etag, last_modified,
                    int(changed), result.elapsed_seconds, now,
                ),
            )
            existing = self.conn.execute(
                "SELECT 1 FROM content_versions WHERE url=? AND content_sha256=?",
                (result.final_url, digest),
            ).fetchone()
            if existing:
                self.conn.execute(
                    "UPDATE content_versions SET last_seen_at=?, seen_count=seen_count+1 WHERE url=? AND content_sha256=?",
                    (now, result.final_url, digest),
                )
            else:
                self.conn.execute(
                    "INSERT INTO content_versions(url, content_sha256, first_seen_at, last_seen_at) VALUES(?,?,?,?)",
                    (result.final_url, digest, now, now),
                )
        return changed

    def save_records(
        self, run_id: str, request: CrawlRequest, records: list[ExtractedRecord],
        *, deduplicate_by: tuple[str, ...] = (),
    ) -> int:
        self._require_run_id(run_id)
        if not records:
            return 0
        inserted = 0
        with self._lock, self.conn:
            for index, record in enumerate(records, 1):
                record_id, stable = storage_identity(run_id, request, record, index, deduplicate_by)
                sql = "INSERT OR IGNORE" if stable else "INSERT OR REPLACE"
                before = self.conn.total_changes
                self.conn.execute(
                    f"""{sql} INTO records(
                        record_id, run_id, request_fingerprint, source_url, record_type,
                        data_json, evidence_json, created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (record_id, run_id, request.fingerprint,
                     record.source_url, record.record_type, json_text(record.data),
                     json_text(record.evidence), utcnow()),
                )
                inserted += self.conn.total_changes - before
        return inserted

    def _observation_context(self, run_id: str) -> tuple[str, str]:
        identity = self.conn.execute("SELECT task_key FROM run_identities WHERE run_id=?", (run_id,)).fetchone()
        task_key = identity["task_key"] if identity is not None else None
        if not task_key:
            row = self.conn.execute("SELECT project_name FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"运行不存在: {run_id}")
            task_key = "legacy:" + str(row["project_name"])
        scope = (self.checkpoint(run_id, "setup", "setup") or {}).get("payload", {}).get("comparison_scope", "")
        return str(task_key), str(scope)

    def _preload_versions(
        self,
        run_id: str,
        records: list[ExtractedRecord],
        identity_fields: tuple[str, ...] = (),
    ) -> dict[tuple[str, str, str], str | None]:
        """Batch-load previous record_versions to eliminate N+1 queries.

        Returns a mapping of (source_url, record_type, identity) -> data_json | None.
        Uses a temporary table + LEFT JOIN so all lookups happen in a single SQL round-trip.
        """
        self._require_run_id(run_id)
        if not records:
            return {}
        keys: list[tuple[str, str, str]] = []
        for record in records:
            identity = record_identity(record.data, record.source_url, identity_fields=identity_fields)
            keys.append((record.source_url, record.record_type, identity))
        seen: set[tuple[str, str, str]] = set()
        unique_keys: list[tuple[str, str, str]] = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)
        result: dict[tuple[str, str, str], str | None] = {k: None for k in keys}
        if not unique_keys:
            return result
        # Batch query via temporary table + LEFT JOIN (single round-trip)
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _rv_lookup("
                          "source_url TEXT, record_type TEXT, identity TEXT, tombstone_key TEXT)")
        self.conn.execute("DELETE FROM _rv_lookup")
        self.conn.executemany(
            "INSERT INTO _rv_lookup VALUES(?,?,?,?)",
            [(*key, entity_checkpoint_key(key[1], key[2])) for key in unique_keys],
        )
        task_key, scope = self._observation_context(run_id)
        rows = self.conn.execute(
            """
            SELECT l.source_url, l.record_type, l.identity, r.data_json, u.rowid AS cycle
            FROM _rv_lookup l
            JOIN entity_observations r
                ON (r.source_url = l.source_url OR ?)
               AND r.record_type = l.record_type AND r.identity = l.identity
               AND r.run_id <> ? AND r.task_key = ? AND r.comparison_scope = ?
            JOIN runs u ON u.run_id=r.run_id
            UNION ALL
            SELECT l.source_url, l.record_type, l.identity, 'null', u.rowid AS cycle
            FROM _rv_lookup l JOIN stage_checkpoints c
              ON c.stage='record_deletion' AND c.idempotency_key=l.tombstone_key
            JOIN runs u ON u.run_id=c.run_id
            JOIN run_identities i ON i.run_id=c.run_id
            WHERE c.run_id<>? AND i.task_key=? AND ?
              AND json_extract(c.payload_json, '$.comparison_scope')=?
            ORDER BY cycle DESC
            """,
            (bool(identity_fields), run_id, task_key, scope, run_id, task_key, bool(identity_fields), scope),
        ).fetchall()
        # Legacy versions are a fallback only for legacy tasks without scoped observations.
        if task_key.startswith("legacy:") and not scope:
            legacy = self.conn.execute(
                "SELECT l.source_url, l.record_type, l.identity, v.data_json "
                "FROM _rv_lookup l JOIN record_versions v ON v.source_url=l.source_url "
                "AND v.record_type=l.record_type AND v.identity=l.identity "
                "JOIN runs u ON u.run_id=v.run_id WHERE v.run_id<>? AND u.project_name=? "
                "ORDER BY v.last_seen_at DESC, v.id DESC",
                (run_id, task_key.removeprefix("legacy:")),
            ).fetchall()
            rows.extend(legacy)
        self.conn.execute("DELETE FROM _rv_lookup")
        seen_results: set[tuple[str, str, str]] = set()
        for row in rows:
            key = (row["source_url"], row["record_type"], row["identity"])
            if key not in seen_results and row["data_json"] is not None:
                seen_results.add(key)
                result[key] = row["data_json"]
        return result

    def _is_first_record_cycle(self, run_id: str) -> bool:
        """Identify the first observation cycle within the stable task and scope.

        Legacy callers retain project-name isolation; explicit IDs survive renaming.
        """
        task_key, scope = self._observation_context(run_id)
        row = self.conn.execute(
            "SELECT 1 FROM entity_observations WHERE run_id<>? AND task_key=? AND comparison_scope=? LIMIT 1",
            (run_id, task_key, scope),
        ).fetchone()
        if row is not None:
            return False
        if task_key.startswith("legacy:") and not scope:
            row = self.conn.execute(
                "SELECT 1 FROM records r JOIN runs u ON u.run_id=r.run_id "
                "WHERE r.run_id<>? AND u.project_name=? LIMIT 1",
                (run_id, task_key.removeprefix("legacy:")),
            ).fetchone()
        return row is None

    def track_semantic_changes(
        self,
        run_id: str,
        records: list[ExtractedRecord],
        *, identity_fields: tuple[str, ...] = (), ignored_fields: set[str] | None = None,
        notification: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Persist semantic record versions and annotate meaningful field-level changes."""

        self._require_run_id(run_id)
        changes: list[dict[str, Any]] = []
        now = utcnow()
        with self._lock, self.conn:
            version_cache = self._preload_versions(run_id, records, identity_fields)
            # 首轮同步：本次是该任务的第一个产出记录的周期 ⇒ 初始记录的 added 属"基线"，
            # 不是"发生了变化"。只标事实，不在数据层判断"要不要提示用户"。
            first_cycle = self._is_first_record_cycle(run_id)
            task_key, scope = self._observation_context(run_id)
            for record in records:
                identity = record_identity(record.data, record.source_url, identity_fields=identity_fields)
                digest = semantic_hash(record.data, ignored_fields=ignored_fields)
                cache_key = (record.source_url, record.record_type, identity)
                before_json = version_cache.get(cache_key)
                before = json.loads(before_json) if before_json else None
                observed = self.conn.execute(
                    "SELECT data_json FROM entity_observations WHERE run_id=? AND record_type=? "
                    "AND identity=? AND (source_url=? OR ?) LIMIT 1",
                    (run_id, record.record_type, identity, record.source_url, bool(identity_fields)),
                ).fetchone()
                if observed is not None:
                    if semantic_hash(json.loads(observed["data_json"]), ignored_fields=ignored_fields) != digest:
                        raise ValueError("同轮业务身份存在冲突值；请复核，不能静默覆盖")
                    before = record.data
                change = compare_record_data(before, record.data, identity=identity, ignored_fields=ignored_fields)
                if first_cycle and change.change_type == "added":
                    change = replace(change, baseline=True)
                change_data = change.to_dict()
                record.evidence["_semantic_change"] = {
                    key: value
                    for key, value in change_data.items()
                    if key not in {"before", "after"}
                }
                if change.change_type != "unchanged":
                    changes.append(change_data)
                    self.conn.execute(
                        """
                        INSERT INTO semantic_changes(
                            run_id, source_url, record_type, identity, change_type,
                            similarity, added_json, removed_json, modified_json,
                            before_json, after_json, baseline, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            run_id,
                            record.source_url,
                            record.record_type,
                            identity,
                            change.change_type,
                            change.similarity,
                            json_text(change.added_fields),
                            json_text(change.removed_fields),
                            json_text(change.modified_fields),
                            json_text(change.before) if change.before is not None else None,
                            json_text(change.after) if change.after is not None else None,
                            1 if change.baseline else 0,
                            now,
                        ),
                    )
                notice_change, suppression = change, ""
                if notification and observed is None:
                    notice_change, suppression = evaluate_policy(
                        self.conn, run_id, record, task_key=task_key, scope=scope, identity=identity,
                        before=before, notification=notification, ignored_fields=ignored_fields,
                        cross_page=bool(identity_fields), now=now, baseline=change.baseline,
                    )
                if (notification and observed is None and not change.baseline
                        and (not suppression or change.change_type in {"added", "modified"})):
                    event_id = uuid.uuid5(uuid.NAMESPACE_URL, json_text(
                        [run_id, task_key, scope, record.record_type, identity, digest])).hex
                    event = {
                        "envelope_version": 1, "source_kind": "record_fields", "event_id": event_id,
                        "rule_id": notification["rule_id"], "rule_name": notification.get("name", "记录变化"),
                        "task_key": task_key, "run_id": run_id, "comparison_scope": scope,
                        "config_sha256": notification["config_sha256"], "url": record.source_url,
                        "detected_at": now, "previous_hash": semantic_hash(notice_change.before) if notice_change.before is not None else None,
                        "current_hash": digest, "previous_content": json_text(notice_change.before) if notice_change.before is not None else None,
                        "current_content": json_text(record.data), "diff_summary": notice_change.change_type,
                        "notification_eligible": not suppression, "suppression_reason": suppression,
                        "details": notice_change.to_dict(), "observed_change_type": change.change_type,
                    }
                    enqueue_event(self.conn, notification["rule_id"], event,
                                  targets=[notification["target_id"]], desktop=False)
                self.conn.execute(
                    "INSERT OR IGNORE INTO entity_observations "
                    "(run_id,task_key,comparison_scope,source_url,record_type,identity,data_json,observed_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, task_key, scope, record.source_url, record.record_type, identity, json_text(record.data), now),
                )
                self.conn.execute(
                    """
                    INSERT INTO record_versions(
                        run_id, source_url, record_type, identity, semantic_sha256,
                        data_json, first_seen_at, last_seen_at, seen_count
                    ) VALUES(?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(source_url, record_type, identity, semantic_sha256)
                    DO UPDATE SET last_seen_at=excluded.last_seen_at,
                                  seen_count=record_versions.seen_count+1
                    """,
                    (
                        run_id,
                        record.source_url,
                        record.record_type,
                        identity,
                        digest,
                        json_text(record.data),
                        now,
                        now,
                    ),
                )
        return changes

    def preserve_reprocess_candidate(
        self, run_id: str, result: FetchResult, records: list[ExtractedRecord],
    ) -> bool:
        """Keep reviewed source records; expose new extraction without positional remapping."""
        with self._lock, self.conn:
            edited = self.conn.execute(
                "SELECT 1 FROM records r JOIN record_edits e ON e.record_id=r.record_id "
                "WHERE r.run_id=? AND r.request_fingerprint=? LIMIT 1",
                (run_id, result.request.fingerprint),
            ).fetchone()
            if edited is None:
                return False
            rows = self.conn.execute(
                "SELECT record_id,evidence_json FROM records WHERE run_id=? AND request_fingerprint=?",
                (run_id, result.request.fingerprint),
            ).fetchall()
            self.save_checkpoint(run_id, "reprocess_candidate", result.request.fingerprint, {
                "content_sha256": result.content_hash,
                "preserved_record_ids": [row["record_id"] for row in rows],
                "records": [{"source_url": r.source_url, "record_type": r.record_type,
                             "data": r.data, "evidence": r.evidence} for r in records],
                "status": "manual_review_required", "mapping": "unconfirmed",
            })
            for row in rows:
                evidence = json.loads(row["evidence_json"])
                evidence.setdefault("_quality", {})["review_required"] = True
                evidence.setdefault("_review", {})["reprocess_candidate"] = result.request.fingerprint
                self.conn.execute("UPDATE records SET evidence_json=? WHERE record_id=?",
                                  (json_text(evidence), row["record_id"]))
            return True

    def reset_record_stage(self, run_id: str) -> dict[str, int]:
        """Clear derived record outputs while preserving responses and raw archives."""

        self._require_run_id(run_id)
        with self._lock, self.conn:
            record_count = self.conn.execute(
                "SELECT COUNT(*) AS n FROM records WHERE run_id=?", (run_id,)
            ).fetchone()["n"]
            quality_count = self.conn.execute(
                "SELECT COUNT(*) AS n FROM quality_stats WHERE run_id=?", (run_id,)
            ).fetchone()["n"]
            # A selector/schema change may reorder or split records. Preserve the entire
            # reviewed source rather than applying edits to a guessed positional match.
            deleted = self.conn.execute(
                "DELETE FROM records WHERE run_id=? AND request_fingerprint NOT IN "
                "(SELECT r.request_fingerprint FROM records r JOIN record_edits e "
                "ON e.record_id=r.record_id WHERE r.run_id=?)", (run_id, run_id),
            ).rowcount
            self.conn.execute("DELETE FROM quality_stats WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM semantic_changes WHERE run_id=?", (run_id,))
            self.conn.execute(
                "DELETE FROM stage_checkpoints WHERE run_id=? AND stage='record_observation'", (run_id,),
            )
            self.conn.execute("DELETE FROM entity_observations WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM record_versions WHERE run_id=?", (run_id,))
        return {"records": int(deleted), "preserved_records": int(record_count - deleted),
                "quality_stats": int(quality_count)}

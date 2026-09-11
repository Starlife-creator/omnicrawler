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
from typing import TYPE_CHECKING, Any

from ..core.models import CrawlRequest, ExtractedRecord, FetchResult
from ..core.utils import json_text, utcnow
from ..quality.semantic_changes import compare_record_data, record_identity, semantic_hash

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

    def save_records(self, run_id: str, request: CrawlRequest, records: list[ExtractedRecord]) -> int:
        self._require_run_id(run_id)
        if not records:
            return 0
        with self._lock, self.conn:
            rows = [
                (
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{request.fingerprint}:{index}").hex,
                    run_id, request.fingerprint, record.source_url,
                    record.record_type, json_text(record.data), json_text(record.evidence), utcnow(),
                )
                for index, record in enumerate(records, 1)
            ]
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO records(
                    record_id, run_id, request_fingerprint, source_url, record_type,
                    data_json, evidence_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        return len(records)

    def _preload_versions(
        self,
        run_id: str,
        records: list[ExtractedRecord],
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
            identity = record_identity(record.data, record.source_url)
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
                          "source_url TEXT, record_type TEXT, identity TEXT)")
        self.conn.execute("DELETE FROM _rv_lookup")
        self.conn.executemany(
            "INSERT INTO _rv_lookup VALUES(?,?,?)",
            unique_keys,
        )
        rows = self.conn.execute(
            """
            SELECT l.source_url, l.record_type, l.identity,
                   r.data_json AS data_json
            FROM _rv_lookup l
            LEFT JOIN record_versions r
                ON r.source_url = l.source_url
               AND r.record_type = l.record_type
               AND r.identity = l.identity
               AND r.run_id <> ?
            ORDER BY r.id DESC
            """,
            (run_id,),
        ).fetchall()
        self.conn.execute("DELETE FROM _rv_lookup")
        seen_results: set[tuple[str, str, str]] = set()
        for row in rows:
            key = (row["source_url"], row["record_type"], row["identity"])
            if key not in seen_results and row["data_json"] is not None:
                seen_results.add(key)
                result[key] = row["data_json"]
        return result

    def track_semantic_changes(
        self,
        run_id: str,
        records: list[ExtractedRecord],
    ) -> list[dict[str, Any]]:
        """Persist semantic record versions and annotate meaningful field-level changes."""

        self._require_run_id(run_id)
        changes: list[dict[str, Any]] = []
        now = utcnow()
        with self._lock, self.conn:
            version_cache = self._preload_versions(run_id, records)
            for record in records:
                identity = record_identity(record.data, record.source_url)
                digest = semantic_hash(record.data)
                cache_key = (record.source_url, record.record_type, identity)
                before_json = version_cache.get(cache_key)
                before = json.loads(before_json) if before_json else None
                change = compare_record_data(before, record.data, identity=identity)
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
                            before_json, after_json, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
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
                            now,
                        ),
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
            self.conn.execute(
                "DELETE FROM record_edits WHERE record_id IN "
                "(SELECT record_id FROM records WHERE run_id=?)",
                (run_id,),
            )
            self.conn.execute("DELETE FROM records WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM quality_stats WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM semantic_changes WHERE run_id=?", (run_id,))
            self.conn.execute("DELETE FROM record_versions WHERE run_id=?", (run_id,))
        return {"records": int(record_count), "quality_stats": int(quality_count)}

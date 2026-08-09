from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from ..core.models import CrawlRequest, ExtractedRecord, FetchResult
from ..core.run_state import TERMINAL_RUN_STATES, canonical_run_state, require_transition
from ..core.utils import json_text, redact_headers, utcnow
from ..quality.semantic_changes import compare_record_data, record_identity, semantic_hash
from .schema import SCHEMA


class _ClosedConnection:
    """S2.5.42：close() 后 conn 的受控占位——任何访问抛可读错误而非 AttributeError。"""

    def __getattr__(self, _name: str) -> Any:
        raise RuntimeError("StateStore 已关闭，禁止继续操作")


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._ensure_response_columns()
        self._lock = threading.RLock()

    def _ensure_response_columns(self) -> None:
        """Upgrade existing 1.0 workspaces without rebuilding their state database."""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(responses)")}
        with self.conn:
            if "etag" not in columns:
                self.conn.execute("ALTER TABLE responses ADD COLUMN etag TEXT")
            if "last_modified" not in columns:
                self.conn.execute("ALTER TABLE responses ADD COLUMN last_modified TEXT")

    def conditional_headers(self, url: str) -> dict[str, str]:
        with self._lock:
            row = self.conn.execute(
                "SELECT etag, last_modified FROM responses "
                "WHERE (final_url=? OR url=?) ORDER BY id DESC LIMIT 1",
                (url, url),
            ).fetchone()
        if row is None:
            return {}
        headers: dict[str, str] = {}
        if row["etag"]:
            headers["If-None-Match"] = str(row["etag"])
        if row["last_modified"]:
            headers["If-Modified-Since"] = str(row["last_modified"])
        return headers

    def close(self) -> None:
        with self._lock:
            if not self.conn or isinstance(self.conn, _ClosedConnection):
                return
            self.conn.close()
            # S2.5.42：关闭后方法调用得到受控 RuntimeError，而非 AttributeError
            self.conn = _ClosedConnection()  # type: ignore[assignment]

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def start_run(self, project_name: str, config_path: str) -> str:
        run_id = uuid.uuid4().hex
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO runs(run_id, project_name, config_path, started_at, status) VALUES(?,?,?,?,?)",
                (run_id, project_name, config_path, utcnow(), "pending"),
            )
            self.conn.execute(
                "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, "pending", "running", "start", "{}", utcnow()),
            )
            self.conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
        return run_id

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        target = canonical_run_state(status)
        if target not in TERMINAL_RUN_STATES:
            raise ValueError(f"完成任务必须使用终态: {target}")
        with self._lock, self.conn:
            row = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"运行不存在: {run_id}")
            current, target = require_transition(str(row["status"]), target)
            self.conn.execute(
                "UPDATE runs SET finished_at=?, status=?, summary_json=? WHERE run_id=?",
                (utcnow(), target, json_text(summary), run_id),
            )
            if current != target:
                self.conn.execute(
                    "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (run_id, current, target, "finish", json_text(summary), utcnow()),
                )

    def transition_run(
        self,
        run_id: str,
        target: str,
        *,
        reason: str = "manual",
        details: dict[str, Any] | None = None,
    ) -> str:
        with self._lock, self.conn:
            row = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"运行不存在: {run_id}")
            current, target = require_transition(str(row["status"]), target)
            if current == target:
                return target
            self.conn.execute("UPDATE runs SET status=? WHERE run_id=?", (target, run_id))
            self.conn.execute(
                "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, current, target, reason, json_text(details or {}), utcnow()),
            )
            return target

    def recover_incomplete_runs(self) -> list[str]:
        """Move crash-interrupted runs to retrying without claiming they succeeded."""

        recovered: list[str] = []
        with self._lock, self.conn:
            rows = self.conn.execute(
                "SELECT run_id, status FROM runs WHERE status IN ('running','paused','retrying')"
            ).fetchall()
            for row in rows:
                current = str(row["status"])
                if current == "paused":
                    self.conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (row["run_id"],))
                    self.conn.execute(
                        "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (row["run_id"], "paused", "running", "crash_recovery", "{}", utcnow()),
                    )
                    current = "running"
                if current == "running":
                    target = "retrying"
                    self.conn.execute("UPDATE runs SET status=? WHERE run_id=?", (target, row["run_id"]))
                    self.conn.execute(
                        "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (row["run_id"], current, target, "crash_recovery", "{}", utcnow()),
                    )
                recovered.append(str(row["run_id"]))
                self.conn.execute(
                    "UPDATE export_commits SET status='retrying', updated_at=? "
                    "WHERE run_id=? AND status='running'",
                    (utcnow(), row["run_id"]),
                )
            self.conn.execute(
                "UPDATE frontier SET status='pending', updated_at=? WHERE status='in_progress'",
                (utcnow(),),
            )
        return recovered

    def save_checkpoint(
        self,
        run_id: str,
        stage: str,
        idempotency_key: str,
        payload: dict[str, Any],
        *,
        status: str = "succeeded",
    ) -> None:
        if not stage.strip() or not idempotency_key.strip():
            raise ValueError("stage和idempotency_key不能为空")
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO stage_checkpoints(run_id, stage, idempotency_key, status, payload_json, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(run_id, stage, idempotency_key) DO UPDATE SET
                    status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (run_id, stage, idempotency_key, status, json_text(payload), utcnow()),
            )

    def checkpoint(self, run_id: str, stage: str, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT status, payload_json, updated_at FROM stage_checkpoints "
                "WHERE run_id=? AND stage=? AND idempotency_key=?",
                (run_id, stage, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "updated_at": row["updated_at"],
        }

    def begin_export(
        self, run_id: str, exporter: str, idempotency_key: str, *, force: bool = False,
    ) -> bool:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO export_commits(idempotency_key, run_id, exporter, status, result_json, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (idempotency_key, run_id, exporter, "running", "{}", utcnow()),
            )
            if cursor.rowcount > 0:
                return True
            if force:
                # S2.5.2：reprocess 强制刷新——已成功的提交也降回 running 重新导出
                cursor = self.conn.execute(
                    "UPDATE export_commits SET status='running', updated_at=? "
                    "WHERE idempotency_key=? AND run_id=? AND exporter=? "
                    "AND status IN ('failed','retrying','succeeded')",
                    (utcnow(), idempotency_key, run_id, exporter),
                )
            else:
                cursor = self.conn.execute(
                    "UPDATE export_commits SET status='running', updated_at=? "
                    "WHERE idempotency_key=? AND run_id=? AND exporter=? AND status IN ('failed','retrying')",
                    (utcnow(), idempotency_key, run_id, exporter),
                )
            return cursor.rowcount > 0

    def finish_export(self, idempotency_key: str, result: dict[str, Any]) -> None:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE export_commits SET status='succeeded', result_json=?, updated_at=? "
                "WHERE idempotency_key=? AND status='running'",
                (json_text(result), utcnow(), idempotency_key),
            )
            if cursor.rowcount != 1:
                raise ValueError("导出提交不存在、已完成或状态无效")

    def fail_export(self, idempotency_key: str, error: str) -> None:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE export_commits SET status='failed', result_json=?, updated_at=? "
                "WHERE idempotency_key=? AND status='running'",
                (json_text({"error": error[:4000]}), utcnow(), idempotency_key),
            )
            if cursor.rowcount != 1:
                raise ValueError("导出提交不存在或状态无效")

    def export_commit(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT run_id, exporter, status, result_json, updated_at FROM export_commits "
                "WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "exporter": row["exporter"],
            "status": row["status"],
            "result": json.loads(row["result_json"]),
            "updated_at": row["updated_at"],
        }

    def prepare_cycle(self, *, reset_all: bool = False) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE frontier SET status='pending', updated_at=? WHERE status='in_progress'", (utcnow(),))
            if reset_all:
                self.conn.execute(
                    "UPDATE frontier SET status='pending', attempts=0, last_error=NULL, updated_at=? WHERE status IN ('done','failed','blocked')",
                    (utcnow(),),
                )

    def retry_failed(self, limit: int | None = None) -> int:
        """Move dead-letter frontier entries back to pending without resetting completed work.

        S2.5.38：分批拉取（每批 1000），大规模失败场景内存可控。
        """
        total = 0
        batch = 1000
        while True:
            with self._lock, self.conn:
                remaining = None if limit is None else max(0, limit - total)
                if remaining == 0:
                    break
                want = batch if remaining is None else min(batch, remaining)
                rows = self.conn.execute(
                    "SELECT fingerprint FROM frontier WHERE status='failed' ORDER BY updated_at LIMIT ?",
                    (want,),
                ).fetchall()
                if not rows:
                    break
                self.conn.executemany(
                    "UPDATE frontier SET status='pending', attempts=0, last_error=NULL, updated_at=? WHERE fingerprint=?",
                    [(utcnow(), row["fingerprint"]) for row in rows],
                )
            total += len(rows)
            if len(rows) < want:
                break
        return total

    def enqueue(self, request: CrawlRequest, *, force: bool = False) -> bool:
        now = utcnow()
        values = (
            request.fingerprint, request.url, request.method.upper(), json_text(redact_headers(request.headers)),
            request.body, request.kind, int(request.render), request.priority, request.depth,
            request.parent_url, json_text(request.meta), now, now,
        )
        with self._lock, self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO frontier(
                    fingerprint, url, method, headers_json, body, kind, render, priority,
                    depth, parent_url, meta_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            inserted = cursor.rowcount > 0
            if force and not inserted:
                # S2.5.42：force 重入队不再重置 attempts（保留重试计数），
                # 仅把状态拉回 pending 并清错误
                self.conn.execute(
                    "UPDATE frontier SET status='pending', last_error=NULL, priority=?, updated_at=? WHERE fingerprint=?",
                    (request.priority, now, request.fingerprint),
                )
            return inserted

    # -- 安全白名单：ORDER BY 从句紧邻 SQL 执行点 --
    _CLAIM_ORDER: dict[str, str] = {
        "bfs": "priority DESC, depth ASC, id ASC",
        "dfs": "depth DESC, priority DESC, id DESC",
        "priority": "priority DESC, depth ASC, id ASC",
        "random": "RANDOM()",
    }

    def claim(self, limit: int, strategy: str = "bfs") -> list[CrawlRequest]:
        order = self._CLAIM_ORDER.get(strategy)
        if order is None:
            order = self._CLAIM_ORDER["bfs"]
        claimed: list[sqlite3.Row] = []
        with self._lock, self.conn:
            # S2.5.3：候选先 SELECT 排序，再用条件 UPDATE（WHERE status='pending'）原子认领；
            # 被并发进程抢走的行 UPDATE 影响 0 行，跳过重取，杜绝 SELECT→UPDATE 双重认领。
            while len(claimed) < limit:
                rows = self.conn.execute(
                    f"SELECT * FROM frontier WHERE status='pending' ORDER BY {order} LIMIT ?",
                    (limit - len(claimed),),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    cursor = self.conn.execute(
                        "UPDATE frontier SET status='in_progress', attempts=attempts+1, updated_at=? "
                        "WHERE fingerprint=? AND status='pending'",
                        (utcnow(), row["fingerprint"]),
                    )
                    if cursor.rowcount == 1:
                        claimed.append(row)
                        if len(claimed) >= limit:
                            break
        return [self._row_to_request(row) for row in claimed]

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> CrawlRequest:
        return CrawlRequest(
            url=row["url"], method=row["method"], headers=json.loads(row["headers_json"]),
            body=row["body"], kind=row["kind"], render=bool(row["render"]),
            priority=float(row["priority"]), depth=int(row["depth"]),
            parent_url=row["parent_url"], meta=json.loads(row["meta_json"]),
        )

    def mark_done(self, fingerprint: str, *, status: str = "done", error: str | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE frontier SET status=?, last_error=?, updated_at=? WHERE fingerprint=?",
                (status, error, utcnow(), fingerprint),
            )

    def mark_failed(self, request: CrawlRequest, exc: Exception, max_attempts: int, retryable: bool = True) -> None:
        with self._lock, self.conn:
            row = self.conn.execute("SELECT attempts FROM frontier WHERE fingerprint=?", (request.fingerprint,)).fetchone()
            retry = bool(row and int(row["attempts"]) < max_attempts and retryable)
            self.conn.execute(
                "UPDATE frontier SET status=?, last_error=?, updated_at=? WHERE fingerprint=?",
                ("pending" if retry else "failed", str(exc)[:4000], utcnow(), request.fingerprint),
            )

    def save_response(self, run_id: str, result: FetchResult, raw_path: str | None) -> bool:
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

    def add_quality_stats(self, run_id: str, field_stats: dict[str, dict[str, int]]) -> None:
        now = utcnow()
        rows = [
            (
                run_id,
                str(field_name),
                int(values.get("total", 0)),
                int(values.get("present", 0)),
                int(values.get("valid", 0)),
                int(values.get("anomalies", 0)),
                now,
            )
            for field_name, values in field_stats.items()
        ]
        if not rows:
            return
        with self._lock, self.conn:
            self.conn.executemany(
                """
                INSERT INTO quality_stats(run_id, field_name, total, present, valid, anomalies, updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(run_id, field_name) DO UPDATE SET
                    total=total+excluded.total,
                    present=present+excluded.present,
                    valid=valid+excluded.valid,
                    anomalies=anomalies+excluded.anomalies,
                    updated_at=excluded.updated_at
                """,
                rows,
            )

    def reset_record_stage(self, run_id: str) -> dict[str, int]:
        """Clear derived record outputs while preserving responses and raw archives."""

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

    def add_audit_event(
        self,
        action: str,
        *,
        run_id: str | None = None,
        actor: str = "system",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not action.strip():
            raise ValueError("Audit action cannot be empty")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO audit_events(run_id, action, actor, details_json, created_at) "
                "VALUES(?,?,?,?,?)",
                (run_id, action, actor, json_text(details or {}), utcnow()),
            )

    def quality_stats(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.rows(
            "SELECT field_name, total, present, valid, anomalies FROM quality_stats "
            "WHERE run_id=? ORDER BY field_name",
            (run_id,),
        )
        for row in rows:
            total = max(1, int(row["total"]))
            present = int(row["present"])
            row["completeness"] = round(present / total, 4)
            row["validation_pass_rate"] = round(int(row["valid"]) / max(1, present), 4)
        return rows

    def review_queue(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """Return low-confidence records without requiring SQLite's optional JSON extension."""
        where, params = (" WHERE run_id=?", (run_id,)) if run_id else ("", ())
        with self._lock:
            rows = self.conn.execute(
                f"SELECT record_id, run_id, source_url, data_json, evidence_json FROM records{where}",
                params,
            ).fetchall()
        queue: list[dict[str, Any]] = []
        for row in rows:
            evidence = json.loads(row["evidence_json"])
            quality = evidence.get("_quality", {}) if isinstance(evidence, dict) else {}
            if quality.get("review_required"):
                queue.append({
                    "record_id": row["record_id"],
                    "run_id": row["run_id"],
                    "source_url": row["source_url"],
                    "data": json.loads(row["data_json"]),
                    "evidence": evidence,
                })
        return queue

    def edit_record(
        self,
        record_id: str,
        field_name: str,
        new_value: Any,
        *,
        actor: str = "local-user",
        reason: str = "manual review",
    ) -> None:
        """Apply a top-level field correction and append an immutable audit event."""
        if not field_name.strip():
            raise ValueError("field_name cannot be empty")
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT data_json, evidence_json FROM records WHERE record_id=?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown record: {record_id}")
            data = json.loads(row["data_json"])
            evidence = json.loads(row["evidence_json"])
            old_value = data.get(field_name)
            data[field_name] = new_value
            edit = {
                "field": field_name,
                "old_value": old_value,
                "new_value": new_value,
                "actor": actor,
                "reason": reason,
                "created_at": utcnow(),
            }
            evidence.setdefault("_review", {}).setdefault("edits", []).append(edit)
            self.conn.execute(
                "UPDATE records SET data_json=?, evidence_json=? WHERE record_id=?",
                (json_text(data), json_text(evidence), record_id),
            )
            self.conn.execute(
                """
                INSERT INTO record_edits(
                    record_id, field_name, old_value_json, new_value_json, actor, reason, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    record_id,
                    field_name,
                    json_text(old_value),
                    json_text(new_value),
                    actor,
                    reason,
                    edit["created_at"],
                ),
            )

    def save_artifact(self, run_id: str, result: FetchResult, path: Path) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO artifacts(
                    run_id, request_fingerprint, source_url, local_path, content_type,
                    size_bytes, sha256, created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (run_id, result.request.fingerprint, result.final_url, str(path), result.content_type, len(result.body), result.content_hash, utcnow()),
            )

    def add_error(self, run_id: str | None, request: CrawlRequest | None, stage: str, exc: Exception, retryable: bool = True) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO errors(run_id, request_fingerprint, url, stage, error_type, message, retryable, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id, request.fingerprint if request else None, request.url if request else None,
                    stage, type(exc).__name__, str(exc)[:4000], int(retryable), utcnow(),
                ),
            )

    def stats(self, run_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            frontier = {row["status"]: row["n"] for row in self.conn.execute("SELECT status, COUNT(*) AS n FROM frontier GROUP BY status")}
            where, params = (" WHERE run_id=?", (run_id,)) if run_id else ("", ())
            responses = self.conn.execute(f"SELECT COUNT(*) AS n FROM responses{where}", params).fetchone()["n"]
            records = self.conn.execute(f"SELECT COUNT(*) AS n FROM records{where}", params).fetchone()["n"]
            errors = self.conn.execute(f"SELECT COUNT(*) AS n FROM errors{where}", params).fetchone()["n"]
            artifacts = self.conn.execute(f"SELECT COUNT(*) AS n FROM artifacts{where}", params).fetchone()["n"]
            semantic_changes = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM semantic_changes{where}", params
            ).fetchone()["n"]
        result = {
            "frontier": frontier, "responses": responses, "records": records,
            "artifacts": artifacts, "errors": errors, "semantic_changes": semantic_changes,
        }
        if run_id:
            result["quality"] = self.quality_stats(run_id)
        return result

    def pending_count(self) -> int:
        """S2.5.37：轻量单表 COUNT（走 idx_frontier_status 索引），
        替代 stats() 的五表全量聚合——高频循环内不再全表扫描。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM frontier WHERE status='pending'"
            ).fetchone()
            return int(row["n"])

    def latest_run(self) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """⚠ 调试/诊断用：执行原始 SQL 查询。

        S2.5.42：只允许只读语句（SELECT/WITH/PRAGMA），拒绝写操作，
        防止注入面被滥用为数据篡改。调用方仍须保证参数化。
        优先使用 set_status / claim / mark_done 等类型安全方法。
        """
        prefix = sql.lstrip().upper()
        if not prefix.startswith(("SELECT", "WITH", "PRAGMA")):
            raise ValueError("rows() 仅允许只读查询（SELECT/WITH/PRAGMA）")
        with self._lock:
            return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

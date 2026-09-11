"""StateStore 的「质量与审核」域 —— 从 state_store.py 抽出的 Mixin（P1-3 第二批）。

覆盖质量统计、审核队列、记录编辑与错误登记：
``add_quality_stats`` / ``quality_stats`` / ``review_queue`` / ``edit_record`` /
``add_error`` / ``stats``。

宿主共享方法（``_require_run_id`` / ``rows``）经 TYPE_CHECKING 声明。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..core.models import CrawlRequest
from ..core.utils import json_text, utcnow

if TYPE_CHECKING:
    import sqlite3


class QualityMixin:
    """质量统计 / 审核队列域。"""

    # ---- 宿主契约 ----
    conn: sqlite3.Connection
    _lock: Any

    if TYPE_CHECKING:
        @staticmethod
        def _require_run_id(run_id: str | None) -> str | None: ...
        def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...
    def add_quality_stats(self, run_id: str, field_stats: dict[str, dict[str, int]]) -> None:
        self._require_run_id(run_id)
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

    def quality_stats(self, run_id: str) -> list[dict[str, Any]]:
        self._require_run_id(run_id)
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

    def review_queue(
        self, run_id: str | None = None, *, limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return low-confidence records without requiring SQLite's optional JSON extension.

        FINAL-D6：`review_required` 判定下推为 evidence_json LIKE 谓词——
        此前全表拉取后逐行 json.loads 过滤，大库上 OOM 风险。LIKE 依赖
        json_text 的默认分隔符（`"review_required": true` 带空格）。
        `limit` 可选限制返回条数（None=全部，保持既有语义）。
        """
        self._require_run_id(run_id)
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit must be an integer or None")
            if limit < 0:
                raise ValueError("limit cannot be negative")
            if limit == 0:
                return []
        clauses: list[str] = ['evidence_json LIKE \'%"review_required": true%\'']
        params: list[Any] = []
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        sql = (
            "SELECT record_id, run_id, source_url, data_json, evidence_json FROM records"
            + " WHERE "
            + " AND ".join(clauses)
        )
        queue: list[dict[str, Any]] = []
        batch_size = 256 if limit is None else max(64, min(1024, limit * 2))
        with self._lock:
            cursor = self.conn.execute(sql, params)
            while rows := cursor.fetchmany(batch_size):
                for row in rows:
                    evidence = json.loads(row["evidence_json"])
                    quality = evidence.get("_quality", {}) if isinstance(evidence, dict) else {}
                    if not quality.get("review_required"):
                        continue
                    queue.append({
                        "record_id": row["record_id"],
                        "run_id": row["run_id"],
                        "source_url": row["source_url"],
                        "data": json.loads(row["data_json"]),
                        "evidence": evidence,
                    })
                    if limit is not None and len(queue) >= limit:
                        return queue
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

"""A-P1-3：复核决策与反馈语料的 SQLite 持久化。

复用既有 ``record_edits`` / ``audit_events`` 表结构，不新增独立表：

- 复核决策（:class:`~omnicrawler.review.review_models.ReviewDecision`）写入
  ``record_edits``（old/new/actor=reviewer/reason=scope），并在 ``audit_events``
  落一条 ``review_decision`` 事件，附带 ``reviewer`` / ``created_at`` / 内容哈希 /
  版本号 / schema 版本；
- 反馈样本（:class:`~omnicrawler.review.review_models.FeedbackSample`）落
  ``audit_events`` 的 ``review_feedback`` 事件，同样带内容哈希与版本号。

内容哈希 = 规范化 JSON 的 SHA-256；版本号按业务键递增（决策按 record_id，
反馈按 sample_id），便于后续做版本化复核与回归语料溯源。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..core.utils import json_text, utcnow
from .review_models import FeedbackSample, ReviewDecision

#: 持久化结构版本：字段口径变化时递增，便于旧库兼容判断。
REVIEW_SCHEMA_VERSION = 1


def content_hash(payload: dict[str, Any]) -> str:
    """规范化（键排序、不转义非 ASCII）JSON 的 SHA-256 十六进制摘要。"""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReviewStore:
    """复核持久化门面：包装 :class:`~omnicrawler.state.StateStore` 连接与锁。"""

    def __init__(self, state: Any) -> None:
        self._state = state

    # ── 复核决策 ─────────────────────────────────────────────────────

    def save_decision(self, decision: ReviewDecision, *, run_id: str | None = None) -> dict[str, Any]:
        """持久化一条复核决策，返回版本号、内容哈希与落库时间。"""
        payload = {
            "record_id": decision.record_id,
            "field": decision.field,
            "old_value": decision.old_value,
            "new_value": decision.new_value,
            "scope": decision.scope,
            "reviewer": decision.reviewer,
        }
        digest = content_hash(payload)
        created_at = utcnow()
        with self._state._lock, self._state.conn:
            row = self._state.conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events "
                "WHERE action='review_decision' AND json_extract(details_json, '$.record_id')=?",
                (decision.record_id,),
            ).fetchone()
            version = int(row["n"]) + 1
            # record_edits 外键约束要求父记录存在；合成复核目标（尚未入库的候选记录）
            # 只写不可变的 audit_events 决策日志，父记录已存在时才同步进 record_edits。
            parent = self._state.conn.execute(
                "SELECT 1 FROM records WHERE record_id=?", (decision.record_id,),
            ).fetchone()
            if parent is not None:
                self._state.conn.execute(
                    """
                    INSERT INTO record_edits(
                        record_id, field_name, old_value_json, new_value_json, actor, reason, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        decision.record_id,
                        decision.field,
                        json_text(decision.old_value),
                        json_text(decision.new_value),
                        decision.reviewer,
                        f"review:{decision.scope}",
                        created_at,
                    ),
                )
            self._state.conn.execute(
                """
                INSERT INTO audit_events(run_id, action, actor, details_json, created_at)
                VALUES(?,?,?,?,?)
                """,
                (
                    run_id,
                    "review_decision",
                    decision.reviewer,
                    json_text({
                        **payload,
                        "version": version,
                        "content_hash": digest,
                        "schema_version": REVIEW_SCHEMA_VERSION,
                    }),
                    created_at,
                ),
            )
        return {"version": version, "content_hash": digest, "created_at": created_at}

    def decisions(self, record_id: str | None = None) -> list[dict[str, Any]]:
        """读取复核决策事件（按时间升序），可按 record_id 过滤。"""
        if record_id is None:
            rows = self._state.rows(
                "SELECT details_json, created_at FROM audit_events "
                "WHERE action='review_decision' ORDER BY id ASC"
            )
        else:
            rows = self._state.rows(
                "SELECT details_json, created_at FROM audit_events "
                "WHERE action='review_decision' AND json_extract(details_json, '$.record_id')=? "
                "ORDER BY id ASC",
                (record_id,),
            )
        return [
            {**json.loads(row["details_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

    # ── 反馈语料 ───────────────────────────────────────────────────────

    def save_feedback(self, sample: FeedbackSample, *, run_id: str | None = None) -> dict[str, Any]:
        """持久化一条反馈样本，返回版本号、内容哈希与落库时间。"""
        payload = {
            "sample_id": sample.sample_id,
            "input_evidence": sample.input_evidence,
            "predicted": sample.predicted,
            "corrected": sample.corrected,
            "rule_or_model": sample.rule_or_model,
            "risk": sample.risk,
            "information_gain": sample.information_gain,
            "approved_for_regression": sample.approved_for_regression,
        }
        digest = content_hash(payload)
        created_at = utcnow()
        with self._state._lock, self._state.conn:
            row = self._state.conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events "
                "WHERE action='review_feedback' AND json_extract(details_json, '$.sample_id')=?",
                (sample.sample_id,),
            ).fetchone()
            version = int(row["n"]) + 1
            self._state.conn.execute(
                """
                INSERT INTO audit_events(run_id, action, actor, details_json, created_at)
                VALUES(?,?,?,?,?)
                """,
                (
                    run_id,
                    "review_feedback",
                    sample.rule_or_model,
                    json_text({
                        **payload,
                        "version": version,
                        "content_hash": digest,
                        "schema_version": REVIEW_SCHEMA_VERSION,
                    }),
                    created_at,
                ),
            )
        return {"version": version, "content_hash": digest, "created_at": created_at}

    def feedback(self, rule_or_model: str | None = None) -> list[dict[str, Any]]:
        """读取反馈样本事件（按时间升序），可按规则/模型过滤。"""
        if rule_or_model is None:
            rows = self._state.rows(
                "SELECT details_json, created_at FROM audit_events "
                "WHERE action='review_feedback' ORDER BY id ASC"
            )
        else:
            rows = self._state.rows(
                "SELECT details_json, created_at FROM audit_events "
                "WHERE action='review_feedback' AND json_extract(details_json, '$.rule_or_model')=? "
                "ORDER BY id ASC",
                (rule_or_model,),
            )
        return [
            {**json.loads(row["details_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

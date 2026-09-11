"""StateStore 的「产物与审计」域 —— 从 state_store.py 抽出的 Mixin（P1-3 第三批）。

覆盖条件头缓存读取、产物落盘与审计事件登记：
``conditional_headers`` / ``save_artifact`` / ``add_audit_event``。

宿主共享方法 ``_require_run_id`` 经 TYPE_CHECKING 声明。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.models import FetchResult
from ..core.utils import json_text, utcnow

if TYPE_CHECKING:
    import sqlite3


class ArtifactsMixin:
    """条件头 / 产物 / 审计域。"""

    # ---- 宿主契约 ----
    conn: sqlite3.Connection
    _lock: Any

    if TYPE_CHECKING:
        @staticmethod
        def _require_run_id(run_id: str | None) -> str | None: ...
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

    def add_audit_event(
        self,
        action: str,
        *,
        run_id: str | None = None,
        actor: str = "system",
        details: dict[str, Any] | None = None,
    ) -> None:
        self._require_run_id(run_id)
        if not action.strip():
            raise ValueError("Audit action cannot be empty")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO audit_events(run_id, action, actor, details_json, created_at) "
                "VALUES(?,?,?,?,?)",
                (run_id, action, actor, json_text(details or {}), utcnow()),
            )

    def save_artifact(self, run_id: str, result: FetchResult, path: Path) -> None:
        self._require_run_id(run_id)
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

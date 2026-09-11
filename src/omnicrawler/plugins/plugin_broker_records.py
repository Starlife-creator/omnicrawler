"""CapabilityBroker 的「records」能力域 —— 从 plugin_broker.py 抽出的 Mixin（P1-3 第三批）。

覆盖固定 SQL 模板的记录读取/分页/写入与响应游标：
- ``_cap_records_read`` / ``_cap_records_page`` / ``_cap_records_write``
- ``_cap_responses_page`` / ``_cap_responses_payload``

以普通 Mixin 形式保留 ``self`` 语义（broker 经 ``dispatch`` 的 getattr 路由调用
``_cap_*``，方法在实例上即可达），宿主属性与调用点不变。
"""
from __future__ import annotations

import base64
import json
import secrets
from typing import Any

from .plugin_broker_contracts import E_CONTRACT, E_INTERNAL, E_QUOTA, E_RESOURCE, CapabilityError


class BrokerRecordsMixin:
    """records / responses 能力域。"""

    # ---- 宿主契约：实例属性（由 CapabilityBroker.__init__ 建立）----
    _state: Any
    _run_id: str
    _project_scope: str
    _plugin_id: str
    _record_cursors: dict[str, dict[str, Any]]
    _response_cursors: dict[str, int]
    _response_refs: dict[str, str]
    def _cap_records_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._state is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 StateStore")
        limit = min(int(payload.get("limit", 100)), 1000)
        # 固定 SQL 模板：不向子进程暴露 rows() 任意 SQL（state_store.py:868
        # 仅宿主内部使用）。source_url 过滤可选。
        sql = (
            "SELECT record_id, source_url, data_json FROM records WHERE run_id=?"
        )
        params: tuple[Any, ...] = (self._run_id,)
        if payload.get("source_url"):
            sql += " AND source_url=?"
            params = (*params, str(payload["source_url"]))
        sql += " ORDER BY rowid DESC LIMIT ?"
        params = (*params, limit)
        rows = self._state.rows(sql, params)
        records = []
        for row in rows:
            try:
                data = json.loads(row["data_json"])
            except (json.JSONDecodeError, KeyError):
                data = {}
            records.append({"record_id": row["record_id"], "source_url": row["source_url"], "data": data})
        return {"records": records, "count": len(records)}

    def _cap_records_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read a stable current-run page without exposing SQL offsets or row ids."""

        if self._state is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 StateStore")
        try:
            limit = int(payload.get("limit", 250))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, "records.page limit 必须是整数") from exc
        if not 1 <= limit <= 1000:
            raise CapabilityError(E_CONTRACT, "records.page limit 必须介于 1 和 1000")
        cursor = str(payload.get("cursor", "")).strip()
        if cursor:
            state = self._record_cursors.pop(cursor, None)
            if state is None:
                raise CapabilityError(E_CONTRACT, "records.page cursor 无效、过期或已使用")
            source_url = state["source_url"]
            last_rowid = int(state["last_rowid"])
        else:
            source_url = str(payload.get("source_url", "")).strip()
            last_rowid = 0
        sql = (
            "SELECT rowid, record_id, source_url, data_json FROM records "
            "WHERE run_id=? AND rowid>?"
        )
        params: tuple[Any, ...] = (self._run_id, last_rowid)
        if source_url:
            sql += " AND source_url=?"
            params = (*params, source_url)
        sql += " ORDER BY rowid ASC LIMIT ?"
        params = (*params, limit + 1)
        rows = list(self._state.rows(sql, params))
        visible = rows[:limit]
        records = []
        for row in visible:
            try:
                data = json.loads(row["data_json"])
            except (json.JSONDecodeError, KeyError, TypeError):
                data = {}
            records.append(
                {
                    "record_id": row["record_id"],
                    "source_url": row["source_url"],
                    "data": data,
                }
            )
        next_cursor = None
        if len(rows) > limit and visible:
            next_cursor = secrets.token_urlsafe(24)
            self._record_cursors[next_cursor] = {
                "source_url": source_url,
                "last_rowid": visible[-1]["rowid"],
            }
        return {
            "records": records,
            "count": len(records),
            "next_cursor": next_cursor,
        }

    def _cap_records_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._state is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 StateStore")
        items = payload.get("records")
        if not isinstance(items, list) or not items:
            raise CapabilityError(E_CONTRACT, "records.write 需要非空 records 数组")
        if len(items) > 1000:
            raise CapabilityError(E_QUOTA, "单次 records.write 上限 1000 条")
        from ..core.models import CrawlRequest, ExtractedRecord

        extracted: list[ExtractedRecord] = []
        for item in items:
            if not isinstance(item, dict):
                raise CapabilityError(E_CONTRACT, "record 必须是 dict")
            extracted.append(
                ExtractedRecord(
                    source_url=str(item.get("source_url", "")),
                    record_type=str(item.get("record_type", "plugin")),
                    data=dict(item.get("data") or {}),
                    evidence=dict(item.get("evidence") or {}),
                )
            )
        request = CrawlRequest(payload.get("source_url", "plugin://records.write"), kind="plugin")
        saved = self._state.save_records(self._run_id, request, extracted)
        return {"saved": saved}

    def _cap_responses_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Page response metadata; archived payload paths never cross the IPC boundary."""

        if self._state is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 StateStore")
        try:
            limit = int(payload.get("limit", 100))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, "responses.page limit 必须是整数") from exc
        if not 1 <= limit <= 500:
            raise CapabilityError(E_CONTRACT, "responses.page limit 必须介于 1 和 500")
        cursor = str(payload.get("cursor", "")).strip()
        if cursor:
            last_id = self._response_cursors.pop(cursor, None)
            if last_id is None:
                raise CapabilityError(E_CONTRACT, "responses.page cursor 无效、过期或已使用")
        else:
            last_id = 0
        rows = list(
            self._state.rows(
                "SELECT id, url, final_url, status_code, content_type, size_bytes, "
                "content_sha256, raw_path, changed, elapsed_seconds, fetched_at "
                "FROM responses WHERE run_id=? AND id>? ORDER BY id ASC LIMIT ?",
                (self._run_id, last_id, limit + 1),
            )
        )
        visible = rows[:limit]
        responses = []
        for row in visible:
            item = {
                "url": row["url"],
                "final_url": row["final_url"],
                "status": row["status_code"],
                "content_type": row["content_type"],
                "size": row["size_bytes"],
                "sha256": row["content_sha256"],
                "changed": bool(row["changed"]),
                "elapsed_seconds": row["elapsed_seconds"],
                "fetched_at": row["fetched_at"],
                "payload_available": bool(row["raw_path"]),
            }
            if row["raw_path"]:
                response_ref = secrets.token_urlsafe(24)
                self._response_refs[response_ref] = str(row["raw_path"])
                item["response_ref"] = response_ref
            responses.append(item)
        next_cursor = None
        if len(rows) > limit and visible:
            next_cursor = secrets.token_urlsafe(24)
            self._response_cursors[next_cursor] = int(visible[-1]["id"])
        return {
            "responses": responses,
            "count": len(responses),
            "next_cursor": next_cursor,
        }

    def _cap_responses_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._state is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 StateStore")
        response_ref = str(payload.get("response_ref", "")).strip()
        raw_path = self._response_refs.get(response_ref)
        if raw_path is None:
            raise CapabilityError(E_CONTRACT, "responses.payload response_ref 无效或过期")
        try:
            maximum = int(payload.get("maximum_bytes", 5 * 1024 * 1024))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, "responses.payload maximum_bytes 必须是整数") from exc
        if not 1 <= maximum <= 16 * 1024 * 1024:
            raise CapabilityError(E_CONTRACT, "responses.payload maximum_bytes 超出 1 B–16 MiB")
        from ..security.paths import require_workspace_path

        try:
            path = require_workspace_path(
                raw_path,
                root=self._state.path.parent,
                what="插件读取响应归档路径",
            )
            with path.open("rb") as stream:
                content = stream.read(maximum + 1)
        except (OSError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, f"响应归档不可读: {exc}") from exc
        truncated = len(content) > maximum
        if truncated:
            content = content[:maximum]
        return {
            "content_b64": base64.b64encode(content).decode("ascii"),
            "size": len(content),
            "truncated": truncated,
        }

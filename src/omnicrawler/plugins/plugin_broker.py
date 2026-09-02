"""宿主侧能力代理（Phase 2a C3/C4）：Capability Broker + IPC 循环驱动。

子进程内**不暴露任何直接网络/文件 API**；所有能力经本 broker 代理：
运行期权限 ⊆ 静态审批（manifest permissions），超出即 E_PERMISSION。

| 插件权限        | 代理操作       | 宿主实现                                   |
|-----------------|----------------|--------------------------------------------|
| records:read    | records.read   | StateStore 固定 SQL 模板（不暴露 rows()）  |
| records:write   | records.write  | StateStore.save_records（批量）            |
| artifacts:read  | artifacts.read | DatasetReader.artifacts()                  |
| network:scoped  | network.fetch  | PluginNetworkClient.fetch（egress 内置）   |
| temp:write      | temp.open      | 会话专属临时目录                           |
| files:read      | files.read     | manifest input_files 白名单（Phase 2b）    |
| resources:read  | resources.*    | 用户明确授权目录的不透明句柄               |
| render:local    | render.html.snapshot | 隔离 Chromium 本地 HTML 快照          |
| surfaces:background | surface.background.* | 宿主拥有的媒体背景表面          |
| （内置）        | system.info    | 宿主版本/平台/后端（无需声明）             |

IPC 循环（drive_loop）：子进程 stdout 混排「能力代理请求」与「handle 响应」，
宿主按 capability 标志分流——capability 请求同步应答（插件在 handle 内阻塞
等待），普通行是 handle 结果（结束本轮调用）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Contract 2 capability protocol versions.  A version changes only when the
# request/response semantics change incompatibly; adding optional fields does
# not bump it.  Plugins discover this mapping through ``system.info`` and may
# declare fail-closed requirements in PLUGIN_METADATA.required_capabilities.
CAPABILITY_VERSIONS: dict[str, int] = {
    "system.info": 1,
    "records.read": 1,
    "records.page": 1,
    "records.write": 1,
    "responses.page": 1,
    "responses.payload": 1,
    "state.get": 1,
    "state.set": 1,
    "state.delete": 1,
    "state.migrate": 1,
    "artifacts.read": 1,
    "artifact.stream.open": 1,
    "artifact.stream.write": 1,
    "artifact.stream.commit": 1,
    "artifact.stream.abort": 1,
    "network.fetch": 1,
    "temp.open": 1,
    "files.read": 1,
    "resources.describe": 1,
    "resources.enumerate": 1,
    "resources.read": 1,
    "render.html.snapshot": 1,
    "render.html.live.start": 1,
    "render.html.live.stop": 1,
    "surface.background.set": 2,
    "surface.background.configure": 2,
    "surface.background.clear": 1,
    "surface.background.capabilities": 1,
    "secrets.get": 1,
}

_CAPABILITY_REQUIREMENT = re.compile(r"^(?:>=)?([1-9][0-9]*)$")


def validate_required_capabilities(required: dict[str, Any]) -> None:
    """Reject a Contract 2 plugin whose broker protocol cannot satisfy it."""

    for name, raw_requirement in required.items():
        capability = str(name).strip()
        match = _CAPABILITY_REQUIREMENT.fullmatch(str(raw_requirement).strip())
        if not capability or match is None:
            raise ValueError(
                f"能力版本要求非法: {name!r}={raw_requirement!r}（仅支持正整数或 >=正整数）"
            )
        minimum = int(match.group(1))
        available = CAPABILITY_VERSIONS.get(capability)
        if available is None:
            raise ValueError(f"宿主不支持插件要求的能力: {capability}")
        if available < minimum:
            raise ValueError(
                f"宿主能力版本不足: {capability}>={minimum}，当前为 {available}"
            )

# 能力 → 所需 manifest 权限（None = 内置，无需声明）
_CAPABILITY_PERMISSIONS: dict[str, str | None] = {
    "records.read": "records:read",
    "records.page": "records:read",
    "records.write": "records:write",
    "responses.page": "responses:read",
    "responses.payload": "responses:payload",
    "state.get": "state:read",
    "state.set": "state:write",
    "state.delete": "state:write",
    "state.migrate": "state:write",
    "artifacts.read": "artifacts:read",
    "artifact.stream.open": "artifacts:write",
    "artifact.stream.write": "artifacts:write",
    "artifact.stream.commit": "artifacts:write",
    "artifact.stream.abort": "artifacts:write",
    "network.fetch": "network:scoped",
    "temp.open": "temp:write",
    "files.read": "files:read",
    "resources.describe": "resources:read",
    "resources.enumerate": "resources:read",
    "resources.read": "resources:read",
    "render.html.snapshot": "render:local",
    "render.html.live.start": "render:scripted",
    "render.html.live.stop": "render:scripted",
    "surface.background.set": "surfaces:background",
    "surface.background.configure": "surfaces:background",
    "surface.background.clear": "surfaces:background",
    "surface.background.capabilities": "surfaces:background",
    # O 例外路径（方案 O2 方案 B）：secrets.get 需 manifest 声明 secrets 白名单；
    # 默认路径是网络经宿主代理密钥零暴露（O2 方案 C），secrets.get 仅显式例外。
    "secrets.get": "secrets:read",
    "system.info": None,
}

E_CONTRACT = "E_CONTRACT"
E_PERMISSION = "E_PERMISSION"
E_QUOTA = "E_QUOTA"
E_RESOURCE = "E_RESOURCE"
E_INTERNAL = "E_INTERNAL"
# Phase 2b J2：data_egress_policy=block 档的共现阻断错误码（C4 权威清单第 73 轮）。
E_EGRESS_BLOCKED = "E_EGRESS_BLOCKED"


class CapabilityError(Exception):
    """带协议错误码的能力代理异常（broker 转成 ok=false 响应）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CapabilityBroker:
    """会话级能力代理：静态审批 ⊇ 运行期请求。"""

    def __init__(
        self,
        *,
        permissions: set[str],
        system_info: dict[str, Any],
        state_store: Any | None = None,
        run_id: str = "",
        dataset_reader: Any | None = None,
        network_client: Any | None = None,
        input_files: tuple[str, ...] = (),
        temp_root: Path | None = None,
        artifact_root: Path | None = None,
        maximum_artifact_bytes: int = 256 * 1024 * 1024,
        secrets_allowlist: tuple[str, ...] = (),
        secret_resolver: Callable[[str], str | None] | None = None,
        audit_hook: Callable[[str, dict[str, Any]], None] | None = None,
        plugin_id: str = "",
        plugin_author_fingerprint: str = "local",
        plugin_state_schema: int = 1,
        project_scope: str = "",
        trace_full: bool = False,
        daily_quota: Any | None = None,
        egress_policy: str = "prompt",
        resource_broker: Any | None = None,
        render_broker: Any | None = None,
        surface_service: Any | None = None,
    ) -> None:
        self._permissions = {p.casefold() for p in permissions}
        self._system_info = dict(system_info)
        self._state = state_store
        self._run_id = run_id
        self._dataset = dataset_reader
        self._network = network_client
        self._input_files = tuple(input_files)
        self._temp_root = Path(temp_root) if temp_root else Path(tempfile.gettempdir())
        self._temp_dir: Path | None = None
        self._artifact_root = Path(artifact_root) if artifact_root else self._temp_root
        self._maximum_artifact_bytes = max(1, int(maximum_artifact_bytes))
        self._artifact_streams: dict[str, dict[str, Any]] = {}
        self.committed_artifacts: list[dict[str, Any]] = []
        self._record_cursors: dict[str, dict[str, Any]] = {}
        self._response_cursors: dict[str, int] = {}
        self._response_refs: dict[str, str] = {}
        # O 密钥零暴露：secrets 白名单 + 宿主解析器（插件进程不可见密钥库）
        self._secrets_allowlist = {str(s) for s in secrets_allowlist}
        self._secret_resolver = secret_resolver
        # C6 审计：audit_hook(action, details)；trace_full=False 时降采样（op_counts）
        self._audit_hook = audit_hook
        self._plugin_id = plugin_id
        self._plugin_author_fingerprint = plugin_author_fingerprint or "local"
        self._plugin_state_schema = int(plugin_state_schema)
        self._project_scope = project_scope
        self._trace_full = trace_full
        self.trace_log: list[dict[str, Any]] = []  # 仅 trace_full 时填充
        # Phase 2b D4.4：每日网络配额（E_QUOTA 来源）；egress_policy 共现检测
        self._daily_quota = daily_quota
        self._egress_policy = egress_policy
        self._resource_broker = resource_broker
        self._render_broker = render_broker
        self._surface_service = surface_service
        # 调用轨迹降采样（C3 第 41 轮）：操作类型计数 + 会话首尾时间
        self.op_counts: dict[str, int] = {}
        self.temp_files_written: list[str] = []

    # ---- 分发 ----

    def dispatch(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation not in _CAPABILITY_PERMISSIONS:
            raise CapabilityError(E_CONTRACT, f"未知能力操作: {operation}")
        self.op_counts[operation] = self.op_counts.get(operation, 0) + 1
        required = _CAPABILITY_PERMISSIONS[operation]
        if required is not None and required not in self._permissions:
            raise CapabilityError(E_PERMISSION, f"未声明权限 {required}（操作 {operation}）")
        handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(
            self, "_cap_" + operation.replace(".", "_")
        )
        started = time.monotonic()
        try:
            result = handler(payload)
        finally:
            self._audit_call(operation, payload, started)
        return result

    def _audit_call(self, operation: str, payload: dict[str, Any], started: float) -> None:
        """C6 审计留痕：每次能力调用记录（不阻断插件运行——钩子异常吞掉）。

        trace_full=False 时降采样（仅 op_counts，已在本方法外累加）；
        trace_full=True 记全序列（operation×时间×数据量，企业审计）。
        """
        duration_ms = int((time.monotonic() - started) * 1000)
        if self._trace_full:
            self.trace_log.append(
                {
                    "operation": operation,
                    "timestamp": time.time(),
                    "payload_bytes": len(json.dumps(payload, ensure_ascii=False)),
                }
            )
        if self._audit_hook is None:
            return
        details = {
            "plugin_id": self._plugin_id,
            "operation": operation,
            "execution_mode": "subprocess",
            "duration_ms": duration_ms,
            "decision": "executed",
        }
        try:
            self._audit_hook("plugin.subprocess.call", details)
        except Exception:  # noqa: BLE001 - 审计写入失败不阻断插件运行（第 35 轮）
            LOGGER.warning("插件审计写入失败（不阻断运行）: plugin=%s op=%s", self._plugin_id, operation)

    def temp_dir(self) -> Path | None:
        return self._temp_dir

    def close(self) -> None:
        """Close and erase every uncommitted opaque artifact stream."""

        for handle in tuple(self._artifact_streams):
            self._abort_artifact_stream(handle)

    # ---- 各能力宿主实现 ----

    def _cap_system_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(self._system_info)
        result["capability_versions"] = dict(CAPABILITY_VERSIONS)
        return result

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

    def _cap_state_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        key = str(payload.get("key", ""))
        try:
            found, value = state.plugin_state_get(namespace, key)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"found": found, "value": value}

    def _cap_state_set(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        key = str(payload.get("key", ""))
        try:
            state.plugin_state_set(namespace, key, payload.get("value"))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"saved": True}

    def _cap_state_delete(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        key = str(payload.get("key", ""))
        try:
            deleted = state.plugin_state_delete(namespace, key)
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"deleted": deleted}

    def _cap_state_migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        state, namespace = self._plugin_state_namespace()
        if str(payload.get("strategy", "copy")) != "copy":
            raise CapabilityError(E_CONTRACT, "state.migrate 当前仅支持显式 copy 策略")
        try:
            source_schema = int(str(payload.get("source_schema", "")))
            copied = state.plugin_state_copy_schema(namespace, source_schema)
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc
        return {"copied": copied, "schema_version": self._plugin_state_schema}

    def _plugin_state_namespace(self) -> tuple[Any, tuple[str, str, str, int]]:
        if self._state is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 StateStore")
        if not self._plugin_id or not self._project_scope or self._plugin_state_schema < 1:
            raise CapabilityError(E_INTERNAL, "宿主未提供完整插件状态命名空间")
        return self._state, (
            self._project_scope,
            self._plugin_id,
            self._plugin_author_fingerprint,
            self._plugin_state_schema,
        )

    def _cap_artifacts_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._dataset is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 DatasetReader")
        infos = self._dataset.artifacts()
        return {"artifacts": [{"name": a.name, "size": a.size_bytes} for a in infos]}

    def _cap_artifact_stream_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        if len(self._artifact_streams) >= 8:
            raise CapabilityError(E_QUOTA, "单个插件会话最多同时打开 8 个工件流")
        name = str(payload.get("name", "")).strip()
        if (
            not name
            or len(name) > 180
            or Path(name).name != name
            or name in {".", ".."}
            or any(char in name for char in "\x00\r\n")
        ):
            raise CapabilityError(E_CONTRACT, "artifact.stream.open 文件名非法")
        media_type = str(payload.get("media_type", "application/octet-stream")).strip()
        if not media_type or len(media_type) > 200 or any(char in media_type for char in "\r\n"):
            raise CapabilityError(E_CONTRACT, "artifact.stream.open media_type 非法")
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        target = self._artifact_root / name
        if target.exists():
            raise CapabilityError(E_RESOURCE, f"工件已存在，拒绝覆盖: {name}")
        handle = secrets.token_urlsafe(24)
        partial = self._artifact_root / f".omnicrawler-{handle}.part"
        try:
            stream = partial.open("xb")
        except OSError as exc:
            raise CapabilityError(E_RESOURCE, f"无法创建工件流: {exc}") from exc
        self._artifact_streams[handle] = {
            "stream": stream,
            "partial": partial,
            "target": target,
            "name": name,
            "media_type": media_type,
            "size": 0,
            "sha256": hashlib.sha256(),
        }
        return {"handle": handle, "maximum_bytes": self._maximum_artifact_bytes}

    def _cap_artifact_stream_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        handle, entry = self._artifact_stream(payload)
        encoded = payload.get("content_b64")
        if not isinstance(encoded, str):
            raise CapabilityError(E_CONTRACT, "artifact.stream.write 需要 content_b64")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, "artifact.stream.write content_b64 非法") from exc
        if len(chunk) > 1024 * 1024:
            raise CapabilityError(E_QUOTA, "单个工件写入分块不得超过 1 MiB")
        new_size = int(entry["size"]) + len(chunk)
        if new_size > self._maximum_artifact_bytes:
            self._abort_artifact_stream(handle)
            raise CapabilityError(E_QUOTA, "工件超过会话允许的最大字节数，未提交内容已删除")
        try:
            entry["stream"].write(chunk)
        except OSError as exc:
            self._abort_artifact_stream(handle)
            raise CapabilityError(E_RESOURCE, f"工件流写入失败: {exc}") from exc
        entry["sha256"].update(chunk)
        entry["size"] = new_size
        return {"written": len(chunk), "size": new_size}

    def _cap_artifact_stream_commit(self, payload: dict[str, Any]) -> dict[str, Any]:
        handle, entry = self._artifact_stream(payload)
        stream = entry["stream"]
        try:
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(entry["partial"], entry["target"])
        except OSError as exc:
            self._abort_artifact_stream(handle)
            raise CapabilityError(E_RESOURCE, f"工件提交失败: {exc}") from exc
        digest = entry["sha256"].hexdigest()
        result = {
            "artifact_id": "sha256:" + digest,
            "name": entry["name"],
            "media_type": entry["media_type"],
            "size": entry["size"],
            "sha256": digest,
        }
        self.committed_artifacts.append({**result, "path": str(entry["target"])})
        del self._artifact_streams[handle]
        return result

    def _cap_artifact_stream_abort(self, payload: dict[str, Any]) -> dict[str, Any]:
        handle, _entry = self._artifact_stream(payload)
        self._abort_artifact_stream(handle)
        return {"aborted": True}

    def _artifact_stream(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        handle = str(payload.get("handle", ""))
        entry = self._artifact_streams.get(handle)
        if entry is None:
            raise CapabilityError(E_CONTRACT, "未知或已关闭的工件流句柄")
        return handle, entry

    def _abort_artifact_stream(self, handle: str) -> None:
        entry = self._artifact_streams.pop(handle, None)
        if entry is None:
            return
        try:
            entry["stream"].close()
        finally:
            try:
                Path(entry["partial"]).unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("未能删除未提交插件工件: %s", entry["partial"])

    def _cap_network_fetch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._network is None:
            raise CapabilityError(E_PERMISSION, "会话未授予网络能力（domains 未声明？）")
        url = str(payload.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise CapabilityError(E_CONTRACT, "network.fetch 仅支持 http(s) URL")
        from ..core.errors import EgressBudgetExceededError, EgressDisabledError

        # Phase 2b D4.4：日级配额检查（E_QUOTA）——与 EgressBroker maximum_requests
        # 会话级配额构成双层量约束。
        if self._daily_quota is not None:
            from .plugin_quota import QuotaExceededError

            try:
                self._daily_quota.check(self._plugin_id)
            except QuotaExceededError as exc:
                raise CapabilityError(E_QUOTA, str(exc)) from exc

        # Phase 2b J2：data_egress_policy 共现检测——records.read 后 fetch 即
        # 潜在数据外传；默认 prompt 提示，block 档阻断（E_EGRESS_BLOCKED）。
        read_calls = (
            self.op_counts.get("records.read", 0)
            + self.op_counts.get("records.page", 0)
            + self.op_counts.get("responses.page", 0)
            + self.op_counts.get("responses.payload", 0)
        )
        if read_calls > 0:
            if self._egress_policy == "block":
                raise CapabilityError(
                    E_EGRESS_BLOCKED,
                    f"data_egress_policy=block：插件在读取 records 后请求网络"
                    f"（共现次数 {read_calls}），阻断数据外传通道",
                )
            self._audit_call_cooccurrence(read_calls)

        try:
            result = self._network.fetch(
                url,
                method=str(payload.get("method", "GET")),
                headers={str(k): str(v) for k, v in (payload.get("headers") or {}).items()},
            )
        except (EgressDisabledError, EgressBudgetExceededError) as exc:
            raise CapabilityError(E_PERMISSION, f"egress 策略拒绝: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - 网络异常收敛为协议错误
            raise CapabilityError(E_RESOURCE, f"请求失败: {exc}") from exc
        finally:
            # 成功/失败都计配额（防恶意重试刷配额；字节仅成功时计）
            if self._daily_quota is not None:
                self._daily_quota.account(
                    self._plugin_id, requests=1, bytes_=0
                )
        import base64

        if self._daily_quota is not None:
            self._daily_quota.account(self._plugin_id, requests=0, bytes_=len(result.body))

        return {
            "status": result.status,
            "url": result.final_url,
            "body_b64": base64.b64encode(result.body).decode("ascii"),
        }

    def _audit_call_cooccurrence(self, read_calls: int) -> None:
        """共现风险留痕（H1 egress_cooccurrence_risk_total 口径的 broker 侧）。"""
        if self._audit_hook is not None:
            try:
                self._audit_hook(
                    "plugin.egress_cooccurrence",
                    {
                        "plugin_id": self._plugin_id,
                        "decision": "cooccurrence_risk",
                        "records_read_before": read_calls,
                    },
                )
            except Exception:  # noqa: BLE001 - 审计失败不阻断
                LOGGER.warning(
                    "共现风险审计写入失败: plugin=%s reads=%s", self._plugin_id, read_calls
                )

    def _cap_temp_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._temp_dir is None:
            self._temp_dir = Path(
                tempfile.mkdtemp(prefix="omnicrawler-plugin-", dir=self._temp_root)
            )
        name = str(payload.get("name", "")).strip()
        if not name or "/" in name or "\\" in name or name.startswith(".."):
            raise CapabilityError(E_CONTRACT, "temp.open 文件名非法")
        target = self._temp_dir / name
        if payload.get("content_b64") is not None:
            import base64

            target.write_bytes(base64.b64decode(str(payload["content_b64"])))
            self.temp_files_written.append(name)
        return {"path": str(target)}

    def _cap_files_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        """files:read（Phase 2b 正式化）：manifest input_files 白名单 + 逃逸拒绝。

        - 请求路径必须命中白名单（精确文件条目 或 目录条目前缀）
        - 解析（含符号链接）后目标必须仍落在白名单根内——链接指向库外 → 拒
        """
        path = str(payload.get("path", ""))
        if not path:
            raise CapabilityError(E_CONTRACT, "files.read 需要 path 参数")
        allowed = [str(item) for item in self._input_files]
        # 白名单命中：精确文件 或 目录前缀（目录条目尾斜杠容忍）
        hit_root: str | None = None
        for item in allowed:
            if path == item:
                hit_root = item
                break
            if path.startswith(item.rstrip("/\\") + "/") or path.startswith(
                item.rstrip("/\\") + "\\"
            ):
                hit_root = item
                break
        if hit_root is None:
            raise CapabilityError(E_PERMISSION, f"路径不在 input_files 白名单: {path}")
        try:
            candidate = Path(path).resolve(strict=True)
        except OSError as exc:
            raise CapabilityError(E_RESOURCE, f"路径解析失败: {exc}") from exc
        # 逃逸校验：解析后目标必须在命中白名单根的解析目录内
        root = Path(hit_root).resolve(strict=False)
        if candidate != root and root not in candidate.parents:
            raise CapabilityError(
                E_PERMISSION,
                f"路径经解析后逃逸白名单: {path} → {candidate}（命中 {hit_root}）",
            )
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise CapabilityError(E_RESOURCE, f"读取失败: {exc}") from exc
        import base64

        return {"content_b64": base64.b64encode(data).decode("ascii"), "size": len(data)}

    def _cap_resources_describe(self, payload: dict[str, Any]) -> dict[str, Any]:
        broker = self._require_resource_broker()
        try:
            return broker.describe(str(payload.get("handle", "")))
        except ValueError as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc

    def _cap_resources_enumerate(self, payload: dict[str, Any]) -> dict[str, Any]:
        broker = self._require_resource_broker()
        try:
            items = broker.enumerate(
                str(payload.get("handle", "")),
                relative=str(payload.get("relative", "")),
                recursive=bool(payload.get("recursive", False)),
                limit=int(payload.get("limit", 500)),
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc
        return {"items": items, "count": len(items)}

    def _cap_resources_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        broker = self._require_resource_broker()
        try:
            data = broker.read(
                str(payload.get("handle", "")),
                str(payload.get("relative", "")),
                maximum_bytes=int(payload.get("maximum_bytes", 4 * 1024 * 1024)),
            )
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc
        return {"content_b64": base64.b64encode(data).decode("ascii"), "size": len(data)}

    def _cap_surface_background_set(self, payload: dict[str, Any]) -> dict[str, Any]:
        surface = self._require_surface_service()
        try:
            render_handle = str(payload.get("render_handle", "")).strip()
            if render_handle:
                surface.set_rendered(self._require_render_broker(), render_handle)
            else:
                surface.set_media(
                    self._require_resource_broker(),
                    str(payload.get("handle", "")),
                    str(payload.get("relative", "")),
                )
        except ValueError as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc
        return {"active": True}

    def _cap_render_html_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        scripted = bool(payload.get("scripted", False))
        if scripted and "render:scripted" not in self._permissions:
            raise CapabilityError(E_PERMISSION, "脚本化本地渲染需要 render:scripted 权限")
        try:
            return self._require_render_broker().snapshot_html(
                self._require_resource_broker(),
                str(payload.get("handle", "")),
                str(payload.get("relative", "")),
                width=int(payload.get("width", 1920)),
                height=int(payload.get("height", 1080)),
                scripted=scripted,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc

    def _cap_render_html_live_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._require_render_broker().start_html_live(
                self._require_resource_broker(),
                str(payload.get("handle", "")),
                str(payload.get("relative", "")),
                width=int(payload.get("width", 1280)),
                height=int(payload.get("height", 720)),
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            raise CapabilityError(E_RESOURCE, str(exc)) from exc

    def _cap_render_html_live_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        self._require_render_broker().stop_live()
        return {"active": False}

    def _cap_surface_background_configure(self, payload: dict[str, Any]) -> dict[str, Any]:
        surface = self._require_surface_service()
        try:
            return dict(surface.configure(dict(payload)))
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc

    def _cap_surface_background_clear(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        surface = self._require_surface_service()
        surface.clear()
        return {"active": False}

    def _cap_surface_background_capabilities(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        del payload
        surface = self._require_surface_service()
        try:
            return dict(surface.capabilities())
        except (TypeError, ValueError) as exc:
            raise CapabilityError(E_CONTRACT, str(exc)) from exc

    def _require_resource_broker(self) -> Any:
        if self._resource_broker is None:
            raise CapabilityError(E_INTERNAL, "宿主未绑定资源授权服务")
        return self._resource_broker

    def _require_surface_service(self) -> Any:
        if self._surface_service is None:
            raise CapabilityError(E_INTERNAL, "当前入口未绑定媒体表面")
        return self._surface_service

    def _require_render_broker(self) -> Any:
        if self._render_broker is None:
            raise CapabilityError(E_INTERNAL, "宿主未绑定隔离渲染服务")
        return self._render_broker

    def _cap_secrets_get(self, payload: dict[str, Any]) -> dict[str, Any]:
        """O 例外路径（方案 O2-B）：secrets.get 显式例外，默认走代理密钥零暴露。

        - manifest 必须声明 secrets 白名单（secrets_allowlist），否则拒绝
        - 仅返回白名单内的 ref；越界 → E_PERMISSION
        - 明文仅在单次调用返回，不缓存；调用即审计（decision=secret_accessed）
        """
        ref = str(payload.get("ref", "")).strip()
        if not ref:
            raise CapabilityError(E_CONTRACT, "secrets.get 需要 ref 参数")
        if ref not in self._secrets_allowlist:
            raise CapabilityError(E_PERMISSION, f"secrets ref 不在 manifest 白名单: {ref}")
        if self._secret_resolver is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供密钥解析器（secrets.get 不可用）")
        value = self._secret_resolver(ref)
        if value is None:
            raise CapabilityError(E_RESOURCE, f"密钥不存在或不可读: {ref}")
        # 审计：密钥访问留痕（decision=secret_accessed，reason=ref；不记录明文）
        if self._audit_hook is not None:
            try:
                self._audit_hook(
                    "plugin.secret_accessed",
                    {"plugin_id": self._plugin_id, "decision": "secret_accessed", "reason": ref},
                )
            except Exception:  # noqa: BLE001 - 审计失败不阻断
                LOGGER.warning("密钥访问审计写入失败: plugin=%s ref=%s", self._plugin_id, ref)
        return {"value": value}


def drive_loop(
    session: Any,
    broker: CapabilityBroker,
    operation: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """宿主侧 IPC 循环：发 handle 请求，混排处理 capability 请求直到响应行。

    session: PluginSubprocessSession（已 start）。
    返回 handle 的最终 result dict；协议/资源错误抛 RuntimeError（带错误码前缀）。
    """
    proc = session._proc  # noqa: SLF001 - 驱动循环需要直接访问管道
    if proc is None or proc.poll() is not None:
        raise RuntimeError(f"{E_RESOURCE}: 插件会话未启动")
    request_id = f"h{next(session._counter)}"
    request = {"v": 1, "operation": operation, "payload": payload, "request_id": request_id}
    timeout = timeout_seconds if timeout_seconds > 0 else session.timeout_seconds
    if session._first_call and session._handshake_timeout:
        timeout = max(timeout, session._handshake_timeout)
        session._first_call = False

    proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    proc.stdin.flush()

    while True:
        line, error = _read_line(proc, timeout)
        if error is not None and isinstance(error, TimeoutError):
            session._kill()
            raise RuntimeError(f"{E_RESOURCE}: 插件响应超时")
        if error is not None:
            session._kill()
            raise RuntimeError(f"{E_RESOURCE}: 插件进程通信失败")
        if not line:
            session._kill()
            raise RuntimeError(f"{E_RESOURCE}: 插件进程意外退出")
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            continue  # 非协议行（插件误写 stdout）静默丢弃
        if message.get("capability"):
            _answer_capability(proc, broker, message)
            continue
        if message.get("request_id") != request_id:
            continue  # 陈旧/错位响应丢弃
        if not message.get("ok", False):
            err = message.get("error", {})
            raise RuntimeError(
                f"{err.get('code', E_INTERNAL)}: {err.get('message', '插件执行失败')}"
            )
        result = message.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"{E_CONTRACT}: 插件返回值必须是对象")
        return result


def _read_line(proc: Any, timeout: float) -> tuple[str, Exception | None]:
    """带超时读一行（后台线程 + join；selectors 在 Windows 不支持管道 fd）。"""
    import threading

    holder: dict[str, Any] = {}

    def _read() -> None:
        try:
            holder["line"] = proc.stdout.readline()
        except OSError as exc:
            holder["error"] = exc

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        return "", TimeoutError("响应超时")
    if "error" in holder:
        return "", holder["error"]
    return holder.get("line", ""), None


def _answer_capability(proc: Any, broker: CapabilityBroker, message: dict[str, Any]) -> None:
    operation = str(message.get("operation", ""))
    payload = message.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    try:
        result = broker.dispatch(operation, payload)
        response = {"request_id": message.get("request_id"), "ok": True, "result": result}
    except CapabilityError as exc:
        response = {
            "request_id": message.get("request_id"),
            "ok": False,
            "error": {"code": exc.code, "message": str(exc)},
        }
    except Exception as exc:  # noqa: BLE001 - broker 内部异常收敛，不炸宿主
        LOGGER.exception("能力代理内部错误: %s", operation)
        response = {
            "request_id": message.get("request_id"),
            "ok": False,
            "error": {"code": E_INTERNAL, "message": str(exc)},
        }
    proc.stdin.write(json.dumps(response, ensure_ascii=False) + "\n")
    proc.stdin.flush()

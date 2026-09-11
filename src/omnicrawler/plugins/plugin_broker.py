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

import json
import logging
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

from .plugin_broker_artifacts import BrokerArtifactsMixin
from .plugin_broker_contracts import (
    _CAPABILITY_PERMISSIONS,
    CAPABILITY_VERSIONS,
    E_CONTRACT,
    E_INTERNAL,
    E_PERMISSION,
    E_RESOURCE,
    CapabilityError,
)
from .plugin_broker_contracts import (
    _CAPABILITY_REQUIREMENT as _CAPABILITY_REQUIREMENT,
)
from .plugin_broker_contracts import (
    E_EGRESS_BLOCKED as E_EGRESS_BLOCKED,
)
from .plugin_broker_contracts import (
    E_QUOTA as E_QUOTA,
)
from .plugin_broker_contracts import (
    validate_required_capabilities as validate_required_capabilities,
)
from .plugin_broker_driver import (
    drive_loop as drive_loop,
)
from .plugin_broker_io import BrokerFilesMixin, BrokerNetworkMixin
from .plugin_broker_records import BrokerRecordsMixin


class CapabilityBroker(
    BrokerArtifactsMixin,
    BrokerFilesMixin,
    BrokerNetworkMixin,
    BrokerRecordsMixin,
):
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



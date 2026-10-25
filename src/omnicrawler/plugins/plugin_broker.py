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
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


def _payload_domain(payload: dict[str, Any]) -> str:
    """从能力调用载荷提取目标域名（A-P3-13 审计留痕用；失败返回空串，不抛错）。"""
    url = payload.get("url") if isinstance(payload, dict) else None
    if not isinstance(url, str) or not url:
        return ""
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""

from .plugin_broker_artifacts import BrokerArtifactsMixin
from .plugin_broker_contracts import (
    _CAPABILITY_PERMISSIONS,
    E_CONTRACT,
    E_PERMISSION,
    CapabilityError,
)
from .plugin_broker_contracts import (
    _CAPABILITY_REQUIREMENT as _CAPABILITY_REQUIREMENT,
)
from .plugin_broker_contracts import (
    CAPABILITY_VERSIONS as CAPABILITY_VERSIONS,
)
from .plugin_broker_contracts import (
    E_EGRESS_BLOCKED as E_EGRESS_BLOCKED,
)
from .plugin_broker_contracts import (
    E_INTERNAL as E_INTERNAL,
)
from .plugin_broker_contracts import (
    E_QUOTA as E_QUOTA,
)
from .plugin_broker_contracts import (
    E_RESOURCE as E_RESOURCE,
)
from .plugin_broker_contracts import (
    validate_required_capabilities as validate_required_capabilities,
)
from .plugin_broker_driver import (
    drive_loop as drive_loop,
)
from .plugin_broker_host import (
    BrokerProgressMixin,
    BrokerSecretsMixin,
    BrokerStateMixin,
    BrokerSurfacesMixin,
    BrokerSystemMixin,
)
from .plugin_broker_io import BrokerFilesMixin, BrokerNetworkMixin
from .plugin_broker_records import BrokerRecordsMixin


class CapabilityBroker(
    BrokerArtifactsMixin,
    BrokerFilesMixin,
    BrokerNetworkMixin,
    BrokerRecordsMixin,
    BrokerProgressMixin,
    BrokerSecretsMixin,
    BrokerStateMixin,
    BrokerSurfacesMixin,
    BrokerSystemMixin,
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
        audit_hook_fail_closed: bool = False,
        daily_quota: Any | None = None,
        egress_policy: str = "prompt",
        resource_broker: Any | None = None,
        render_broker: Any | None = None,
        surface_service: Any | None = None,
        progress_relay: Any | None = None,
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
        # A-P3-13：默认模式即逐条留痕（operation/时间戳/时长/域名/字节数，不含 payload 内容）；
        # trace_full 仅提升审计粒度标记，不再决定是否留痕。
        self._audit_hook_fail_closed = audit_hook_fail_closed
        self.trace_log: list[dict[str, Any]] = []
        # Phase 2b D4.4：每日网络配额（E_QUOTA 来源）；egress_policy 共现检测
        self._daily_quota = daily_quota
        self._egress_policy = egress_policy
        self._resource_broker = resource_broker
        self._render_broker = render_broker
        self._surface_service = surface_service
        # U6（2026-09-24）：运行进度投递桥（线程安全的一次投递，可为 None）
        self._progress_relay = progress_relay
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
        """A-P3-13 审计留痕：默认逐条记录元数据（operation/时间戳/时长/域名/字节数），
        不落 payload 内容；``trace_full`` 仅作为粒度标记透传给审计报告。

        审计钩子异常默认按既有口径吞掉并告警（不阻断插件运行）；
        ``audit_hook_fail_closed=True`` 时改为 fail-closed 重新抛出。
        """
        duration_ms = int((time.monotonic() - started) * 1000)
        domain = _payload_domain(payload)
        payload_bytes = len(json.dumps(payload, ensure_ascii=False, default=str))
        self.trace_log.append(
            {
                "operation": operation,
                "timestamp": time.time(),
                "duration_ms": duration_ms,
                "domain": domain,
                "payload_bytes": payload_bytes,
                "trace_full": self._trace_full,
            }
        )
        if self._audit_hook is None:
            return
        details = {
            "plugin_id": self._plugin_id,
            "operation": operation,
            "execution_mode": "subprocess",
            "duration_ms": duration_ms,
            "domain": domain,
            "payload_bytes": payload_bytes,
            "trace_full": self._trace_full,
            "decision": "executed",
        }
        try:
            self._audit_hook("plugin.subprocess.call", details)
        except Exception as exc:  # noqa: BLE001 - 审计钩子异常按配置处置
            if self._audit_hook_fail_closed:
                LOGGER.error(
                    "插件审计钩子失败（fail-closed 配置：抛出阻断）: plugin=%s op=%s",
                    self._plugin_id, operation,
                )
                raise
            LOGGER.warning("插件审计写入失败（不阻断运行）: plugin=%s op=%s", self._plugin_id, operation)

    def temp_dir(self) -> Path | None:
        return self._temp_dir

    def close(self) -> None:
        """Close and erase every uncommitted opaque artifact stream."""

        for handle in tuple(self._artifact_streams):
            self._abort_artifact_stream(handle)

    # ---- 各能力宿主实现 ----



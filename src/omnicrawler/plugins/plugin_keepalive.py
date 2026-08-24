"""keepalive 长驻会话池（Phase 2b N3：跨 run 复用 + hook 事件分发）。

session 模式（C1a）已摊薄单 run 内多次调用的启动成本；keepalive 提供
「跨 run 复用」增量——长驻进程在多个 run 间存活，idle 超时回收（默认
10min）释放并发名额（N3/第 51 轮：keepalive 计入 subprocess_max_concurrent）。

hook 放开条件（N3 正式约束）：keepalive 落地 + 事件载荷经代理（hook 参数
均为纯数据，无宿主对象引用）；hooks 权限纳入静态审批。

实现：
- KeepaliveSessionPool：按 (plugin_id) 持有长驻 _SubprocessSessionHost；
  acquire() 复用或新建（受并发上限约束）；release() 归还；idle 超时回收。
- 进程复用判定：宿主侧仅需确认 session 仍在运行（pid 存活），不校验代码
  版本——插件目录不可变（B01-010 锁文件防篡改），run 间复用安全。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .plugin_subprocess_adapter import _SubprocessSessionHost

LOGGER = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT_SECONDS = 600  # 10min（N3：idle 超时回收）


@dataclass
class _IdleEntry:
    host: _SubprocessSessionHost
    idle_since: float = field(default_factory=time.monotonic)


class KeepaliveSessionPool:
    """跨 run 长驻会话池：复用 + idle 回收 + 并发上限（默认 4）。"""

    def __init__(
        self,
        *,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        max_concurrent: int = 4,
    ) -> None:
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds 必须为正")
        self._idle_timeout = idle_timeout_seconds
        self._max_concurrent = max_concurrent
        self._lock = threading.Lock()
        # plugin_id -> 空闲条目（LRU 顺序，回收优先最久未用）
        self._idle: OrderedDict[str, _IdleEntry] = OrderedDict()
        self._active: set[str] = set()
        self._closed = False

    # ---- 对外接口 ----

    def acquire(
        self,
        plugin_id: str,
        *,
        plugin_root: Path,
        entry_module: str,
        permissions: set[str],
        input_files: tuple[str, ...] = (),
        config: Any | None = None,
        timeout_seconds: float = 30.0,
        verified_bytes: bytes | None = None,
        state_store: Any | None = None,
        run_id: str = "",
        dataset_reader: Any | None = None,
        network_client: Any | None = None,
        daily_quota: Any | None = None,
        egress_policy: str = "prompt",
        secrets_allowlist: tuple[str, ...] = (),
        secret_resolver: Any | None = None,
        audit_hook: Any | None = None,
    ) -> _SubprocessSessionHost:
        """取得长驻会话 host（复用或新建）；占用并发名额。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("keepalive 会话池已关闭")
            if self._active_count() >= self._max_concurrent:
                raise RuntimeError(
                    "keepalive 并发已达上限 "
                    f"({self._max_concurrent}，含长驻会话；请等待 idle 回收或调大 "
                    "plugins.subprocess_max_concurrent)"
                )
            entry = self._idle.pop(plugin_id, None)
            if entry is not None:
                host = entry.host
                # 运行期重新绑定 run 相关依赖（run_id/state/网络/审计随 run 变）
                host._run_id = run_id
                host._state_store = state_store
                host._network_client = network_client
                host._dataset_reader = dataset_reader
                host._broker = None  # 强制下次 _ensure 重建 broker（新 run 依赖）
                LOGGER.debug("keepalive 复用会话: %s", plugin_id)
            else:
                host = _SubprocessSessionHost(
                    plugin_root,
                    entry_module,
                    permissions=permissions,
                    input_files=input_files,
                    config=config,
                    timeout_seconds=timeout_seconds,
                    verified_bytes=verified_bytes,
                    state_store=state_store,
                    run_id=run_id,
                    dataset_reader=dataset_reader,
                    network_client=network_client,
                    plugin_id=plugin_id,
                    secrets_allowlist=secrets_allowlist,
                    secret_resolver=secret_resolver,
                    audit_hook=audit_hook,
                    daily_quota=daily_quota,
                    egress_policy=egress_policy,
                )
                LOGGER.info("keepalive 新建长驻会话: %s", plugin_id)
            self._active.add(plugin_id)
            return host

    def release(self, plugin_id: str, host: _SubprocessSessionHost) -> None:
        """归还空闲会话（不回收进程，等待 idle 超时）。"""
        with self._lock:
            if plugin_id in self._active:
                self._active.discard(plugin_id)
            # 会话已死（进程退出）→ 直接丢弃
            session = host._session
            if session is None or session._proc is None or session._proc.poll() is not None:
                host.close()
                return
            self._idle[plugin_id] = _IdleEntry(host=host)
            self._idle.move_to_end(plugin_id)

    def reap(self) -> int:
        """回收 idle 超时会话；返回回收数（每次 acquire/调用方周期触发）。"""
        now = time.monotonic()
        with self._lock:
            stale = [
                key
                for key, entry in self._idle.items()
                if now - entry.idle_since >= self._idle_timeout
            ]
            for key in stale:
                entry = self._idle.pop(key)
                try:
                    entry.host.close()
                except Exception:  # noqa: BLE001 - 回收失败不阻断
                    LOGGER.warning("keepalive 回收失败: %s", key)
                LOGGER.info("keepalive idle 超时回收: %s", key)
        return len(stale)

    def close_all(self) -> None:
        """关闭全部会话（宿主退出）。"""
        with self._lock:
            self._closed = True
            for entry in list(self._idle.values()):
                try:
                    entry.host.close()
                except Exception:  # noqa: BLE001
                    pass
            self._idle.clear()
            self._active.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "idle": len(self._idle),
                "active": len(self._active),
                "max_concurrent": self._max_concurrent,
                "idle_timeout_seconds": int(self._idle_timeout),
            }

    # ---- 内部 ----

    def _active_count(self) -> int:
        return len(self._active) + len(self._idle)


def dispatch_hook_event(
    host: _SubprocessSessionHost,
    event: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """hook 事件分发到长驻会话（N3：事件载荷均为纯数据，无宿主对象引用）。

    经 host.call 走 handle("hook.<event>", payload)——插件侧从 payload 取
    事件名与数据，返回结果 dict；hook 权限由宿主静态审批面保证。
    """
    return host.call(f"hook.{event}", payload)

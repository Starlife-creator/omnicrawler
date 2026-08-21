"""子进程组件适配器（Phase 2a B4 集成层）：把 pipeline 接口桥接到契约 2 会话。

契约 2 插件以 ``handle(operation, payload) -> dict`` 形态运行于子进程；pipeline
则以 ``source.seed() -> list[CrawlRequest]`` / ``fetcher.fetch(request) -> FetchResult``
消费组件。本模块提供两个适配器，把宿主侧接口翻译为经
:class:`PluginSubprocessSession` 的会话调用——pipeline 无需感知子进程边界。

序列化边界（跨进程只传 JSON）：
- CrawlRequest ↔ dict（body 经 base64）
- FetchResult ← dict（body 经 base64）

生命周期：适配器自持会话——首次调用时懒 spawn（C1a：多次 handle 复用同一
进程），close() 显式收尾。registry 以工厂形态注册（``lambda config: adapter``），
pipeline 无感知子进程边界。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from ..core.models import CrawlRequest, FetchResult
from .plugin_broker import CapabilityBroker, drive_loop
from .plugin_sandbox import PluginSubprocessSession


def _system_info(config: Any | None) -> dict[str, Any]:
    from .. import __version__
    from . import plugin_backend

    return {
        "version": __version__,
        "backend": plugin_backend.backend_name(),
        "platform": __import__("sys").platform,
    }


def _build_broker(
    *,
    permissions: set[str],
    input_files: tuple[str, ...],
    config: Any | None,
    state_store: Any | None,
    run_id: str,
    dataset_reader: Any | None,
    network_client: Any | None,
) -> CapabilityBroker:
    return CapabilityBroker(
        permissions=permissions,
        system_info=_system_info(config),
        state_store=state_store,
        run_id=run_id,
        dataset_reader=dataset_reader,
        network_client=network_client,
        input_files=input_files,
    )


class _SubprocessSessionHost:
    """会话生命周期持有者：懒 spawn + close() 收尾 + 能力代理桥接。"""

    def __init__(
        self,
        plugin_root: Path,
        entry_module: str,
        *,
        permissions: set[str],
        input_files: tuple[str, ...] = (),
        config: Any | None = None,
        timeout_seconds: float = 30.0,
        verified_bytes: bytes | None = None,
        state_store: Any | None = None,
        run_id: str = "",
        dataset_reader: Any | None = None,
        network_client: Any | None = None,
    ) -> None:
        self._plugin_root = plugin_root
        self._entry_module = entry_module
        self._permissions = permissions
        self._input_files = input_files
        self._config = config
        self._timeout = timeout_seconds
        self._verified_bytes = verified_bytes
        self._state_store = state_store
        self._run_id = run_id
        self._dataset_reader = dataset_reader
        self._network_client = network_client
        self._session: PluginSubprocessSession | None = None
        self._broker: CapabilityBroker | None = None

    def _ensure(self) -> tuple[PluginSubprocessSession, CapabilityBroker]:
        if self._session is None or self._session._proc is None:  # noqa: SLF001
            self._session = PluginSubprocessSession(
                self._plugin_root,
                self._entry_module,
                timeout_seconds=self._timeout,
                verified_bytes=self._verified_bytes,
            )
            self._session.start()
            self._broker = _build_broker(
                permissions=self._permissions,
                input_files=self._input_files,
                config=self._config,
                state_store=self._state_store,
                run_id=self._run_id,
                dataset_reader=self._dataset_reader,
                network_client=self._network_client,
            )
        assert self._broker is not None
        return self._session, self._broker

    def call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        session, broker = self._ensure()
        return drive_loop(session, broker, operation, payload, timeout_seconds=0)

    def close(self) -> None:
        if self._session is not None:
            self._session.end()
            self._session = None
            self._broker = None

    def __del__(self) -> None:  # pragma: no cover - 兜底清理
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


def request_to_dict(request: CrawlRequest) -> dict[str, Any]:
    """CrawlRequest → JSON 可传 dict（body 经 base64）。"""
    return {
        "url": request.url,
        "method": request.method,
        "headers": dict(request.headers),
        "body_b64": base64.b64encode(request.body).decode("ascii") if request.body else None,
        "kind": request.kind,
        "render": request.render,
        "priority": request.priority,
        "depth": request.depth,
        "parent_url": request.parent_url,
        "meta": dict(request.meta),
    }


def dict_to_request(payload: dict[str, Any]) -> CrawlRequest:
    """dict → CrawlRequest（容错：缺失字段回退默认值）。"""
    body = payload.get("body_b64")
    return CrawlRequest(
        url=str(payload.get("url", "")),
        method=str(payload.get("method", "GET")),
        headers={str(k): str(v) for k, v in (payload.get("headers") or {}).items()},
        body=base64.b64decode(body) if body else None,
        kind=str(payload.get("kind", "page")),
        render=bool(payload.get("render", False)),
        priority=float(payload.get("priority", 0.0)),
        depth=int(payload.get("depth", 0)),
        parent_url=payload.get("parent_url"),
        meta=dict(payload.get("meta") or {}),
    )


def dict_to_result(payload: dict[str, Any], request: CrawlRequest) -> FetchResult:
    """dict → FetchResult（body 经 base64）。"""
    body = payload.get("body_b64")
    return FetchResult(
        request=request,
        final_url=str(payload.get("url", payload.get("final_url", request.url))),
        status=int(payload.get("status", 200)),
        headers={str(k): str(v) for k, v in (payload.get("headers") or {}).items()},
        body=base64.b64decode(body) if body else b"",
        elapsed_seconds=float(payload.get("elapsed_seconds", 0.0)),
        meta=dict(payload.get("meta") or {}),
    )


class SubprocessSourceAdapter:
    """契约 2 source：``seed()`` 经会话调用 ``handle("source.seed", ...)``。

    工厂形态兼容 pipeline：``registry.sources[name](config)`` 传入 config 时
    以 config 覆盖构造参数（懒绑定）。也支持直接传入 config 实例。
    """

    def __init__(
        self,
        host: _SubprocessSessionHost,
        config: Any | None = None,
    ) -> None:
        self._host = host
        # pipeline 以工厂方式调用时传入 config——记录但不重建会话（host 已持有）
        if config is not None:
            self._host._config = config

    def seed(self) -> list[CrawlRequest]:
        payload: dict[str, Any] = {}
        cfg = self._host._config
        if cfg is not None and hasattr(cfg, "section"):
            section = cfg.section("source")
            payload["config"] = dict(section) if isinstance(section, dict) else {}
        result = self._host.call("source.seed", payload)
        raw = result.get("requests", [])
        if not isinstance(raw, list):
            return []
        return [dict_to_request(item) for item in raw if isinstance(item, dict)]

    def close(self) -> None:
        self._host.close()


class SubprocessFetcherAdapter:
    """契约 2 fetcher：``fetch(request)`` 经会话调用 ``handle("fetcher.fetch", ...)``。"""

    def __init__(self, host: _SubprocessSessionHost, config: Any | None = None) -> None:
        self._host = host
        if config is not None:
            self._host._config = config

    def fetch(self, request: CrawlRequest) -> FetchResult:
        payload = {"request": request_to_dict(request)}
        result = self._host.call("fetcher.fetch", payload)
        return dict_to_result(result, request)

    def close(self) -> None:
        self._host.close()

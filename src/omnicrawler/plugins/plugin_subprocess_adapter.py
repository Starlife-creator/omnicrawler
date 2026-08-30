"""子进程组件适配器（Phase 2a B4 集成层）：把 pipeline 接口桥接到契约 2 会话。

契约 2 插件以 ``handle(operation, payload) -> dict`` 形态运行于子进程；pipeline
则以 source/fetcher/processor/exporter/hook 的宿主接口消费组件。本模块提供
对应适配器，把宿主侧接口翻译为经
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
import hashlib
import threading
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from typing import Any

from ..core.models import CrawlRequest, ExtractedRecord, FetchResult, ProcessResult
from .plugin_broker import CapabilityBroker, drive_loop
from .plugin_sandbox import PluginSubprocessSession

CONTRACT2_HOOK_EVENTS = (
    "before_run",
    "before_fetch",
    "after_fetch",
    "after_extract",
    "before_export",
    "after_export",
    "after_run",
    "on_error",
    "before_reprocess",
    "after_reprocess",
)

_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "proxy-authorization", "set-cookie", "x-api-key"}
)


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
    secrets_allowlist: tuple[str, ...] = (),
    secret_resolver: Any | None = None,
    audit_hook: Any | None = None,
    plugin_id: str = "",
    trace_full: bool = False,
    daily_quota: Any | None = None,
    egress_policy: str = "prompt",
) -> CapabilityBroker:
    return CapabilityBroker(
        permissions=permissions,
        system_info=_system_info(config),
        state_store=state_store,
        run_id=run_id,
        dataset_reader=dataset_reader,
        network_client=network_client,
        input_files=input_files,
        secrets_allowlist=secrets_allowlist,
        secret_resolver=secret_resolver,
        audit_hook=audit_hook,
        plugin_id=plugin_id,
        trace_full=trace_full,
        daily_quota=daily_quota,
        egress_policy=egress_policy,
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
        plugin_id: str = "",
        secrets_allowlist: tuple[str, ...] = (),
        secret_resolver: Any | None = None,
        audit_hook: Any | None = None,
        trace_full: bool = False,
        daily_quota: Any | None = None,
        egress_policy: str = "prompt",
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
        self._plugin_id = plugin_id
        self._secrets_allowlist = secrets_allowlist
        self._secret_resolver = secret_resolver
        self._audit_hook = audit_hook
        self._trace_full = trace_full
        self._daily_quota = daily_quota
        self._egress_policy = egress_policy
        self._session: PluginSubprocessSession | None = None
        self._broker: CapabilityBroker | None = None
        self._call_lock = threading.RLock()

    def _ensure(self) -> tuple[PluginSubprocessSession, CapabilityBroker]:
        if self._session is None or self._session._proc is None:  # noqa: SLF001
            self._session = PluginSubprocessSession(
                self._plugin_root,
                self._entry_module,
                timeout_seconds=self._timeout,
                verified_bytes=self._verified_bytes,
            )
            self._session.start()
            self._broker = None  # 新建会话必重建 broker（run 依赖可能变化）
        if self._broker is None:
            self._broker = _build_broker(
                permissions=self._permissions,
                input_files=self._input_files,
                config=self._config,
                state_store=self._state_store,
                run_id=self._run_id,
                dataset_reader=self._dataset_reader,
                network_client=self._network_client,
                secrets_allowlist=self._secrets_allowlist,
                secret_resolver=self._secret_resolver,
                audit_hook=self._audit_hook,
                plugin_id=self._plugin_id,
                trace_full=self._trace_full,
                daily_quota=self._daily_quota,
                egress_policy=self._egress_policy,
            )
        assert self._broker is not None
        return self._session, self._broker

    def call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        # 一个 JSON-RPC 会话同一时刻只能有一个在途请求；多线程抓取与 hook
        # 可能共享同一插件 host，因此在宿主侧串行化帧读写。
        with self._call_lock:
            session, broker = self._ensure()
            return drive_loop(session, broker, operation, payload, timeout_seconds=0)

    def close(self) -> None:
        with self._call_lock:
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


def result_to_dict(result: FetchResult, *, include_body: bool = True) -> dict[str, Any]:
    """FetchResult → JSON 可传 dict；hook 场景可只传摘要而不复制正文。"""
    request_payload = request_to_dict(result.request)
    request_payload["headers"] = _redact_headers(request_payload["headers"])
    # processor 需要响应正文，但不需要可能包含表单或密钥的原始请求体。
    request_payload.pop("body_b64", None)
    payload: dict[str, Any] = {
        "request": request_payload,
        "final_url": result.final_url,
        "status": result.status,
        "headers": _redact_headers(result.headers),
        "elapsed_seconds": result.elapsed_seconds,
        "meta": dict(result.meta),
        "content_type": result.content_type,
        "content_hash": result.content_hash,
        "body_bytes": len(result.body),
    }
    if include_body:
        payload["body_b64"] = base64.b64encode(result.body).decode("ascii")
    return payload


def dict_to_record(payload: dict[str, Any]) -> ExtractedRecord:
    return ExtractedRecord(
        source_url=str(payload.get("source_url", "")),
        record_type=str(payload.get("record_type", "record")),
        data=dict(payload.get("data") or {}),
        evidence=dict(payload.get("evidence") or {}),
    )


def record_to_dict(record: ExtractedRecord) -> dict[str, Any]:
    return {
        "source_url": record.source_url,
        "record_type": record.record_type,
        "data": dict(record.data),
        "evidence": dict(record.evidence),
    }


def _redact_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): "<redacted>" if str(key).casefold() in _SENSITIVE_HEADERS else str(value)
        for key, value in headers.items()
    }


def _hook_json_value(value: Any) -> Any:
    """把生命周期上下文收敛为无宿主引用、无认证头的 JSON 数据。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, Path):
        return value.name
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    if isinstance(value, CrawlRequest):
        payload = request_to_dict(value)
        payload["headers"] = _redact_headers(payload["headers"])
        payload.pop("body_b64", None)
        return payload
    if isinstance(value, FetchResult):
        payload = result_to_dict(value, include_body=False)
        payload["headers"] = _redact_headers(payload["headers"])
        payload["request"] = _hook_json_value(value.request)
        return payload
    if isinstance(value, ExtractedRecord):
        return record_to_dict(value)
    if isinstance(value, dict):
        return {str(key): _hook_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_hook_json_value(item) for item in value]
    if is_dataclass(value):
        return _hook_json_value(asdict(value))
    # pipeline/state/config 等宿主对象不得越过隔离边界。
    return {"type": type(value).__name__}


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


class SubprocessProcessorAdapter:
    """契约 2 processor/parser/extractor：``process(result)`` 纯数据桥接。"""

    def __init__(
        self,
        host: _SubprocessSessionHost,
        config: Any | None = None,
        options: dict[str, Any] | None = None,
        *,
        operation: str = "processor.process",
    ) -> None:
        self._host = host
        self._operation = operation
        self._options = dict(options or {})
        if config is not None:
            self._host._config = config

    def process(self, result: FetchResult) -> ProcessResult:
        response = self._host.call(
            self._operation,
            {"result": result_to_dict(result), "options": self._options},
        )
        raw_records = response.get("records", [])
        raw_requests = response.get("requests", [])
        if not isinstance(raw_records, list) or not isinstance(raw_requests, list):
            raise TypeError(f"{self._operation} 必须返回 records/requests 数组")
        return ProcessResult(
            records=[dict_to_record(item) for item in raw_records if isinstance(item, dict)],
            requests=[dict_to_request(item) for item in raw_requests if isinstance(item, dict)],
            artifact_path=(
                str(response["artifact_path"])
                if response.get("artifact_path") is not None
                else None
            ),
        )

    def close(self) -> None:
        self._host.close()


class SubprocessAuthProviderAdapter:
    """契约 2 auth_provider：准备请求，但不暴露宿主配置或网络客户端。"""

    def __init__(
        self,
        host: _SubprocessSessionHost,
        config: Any | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._host = host
        self._options = dict(options or {})
        if config is not None:
            self._host._config = config

    def prepare(self, request: CrawlRequest) -> CrawlRequest | None:
        request_payload = request_to_dict(request)
        request_payload["headers"] = _redact_headers(request_payload["headers"])
        request_payload.pop("body_b64", None)
        request_payload["body_bytes"] = len(request.body or b"")
        request_payload["body_sha256"] = hashlib.sha256(request.body or b"").hexdigest()
        response = self._host.call(
            "auth.prepare",
            {"request": request_payload, "options": self._options},
        )
        prepared = response.get("request")
        if prepared is None:
            return None
        if not isinstance(prepared, dict):
            raise TypeError("auth.prepare 的 request 必须是对象或 null")
        prepared_request = dict_to_request(prepared)
        merged_headers = dict(request.headers)
        for key, value in prepared_request.headers.items():
            if str(key).casefold() in _SENSITIVE_HEADERS and value == "<redacted>":
                continue
            merged_headers[str(key)] = str(value)
        return replace(
            prepared_request,
            headers=merged_headers,
            body=(prepared_request.body if "body_b64" in prepared else request.body),
        )

    def close(self) -> None:
        self._host.close()


class SubprocessTransformerAdapter:
    """契约 2 transformer：逐条记录转换，返回 record/data/null。"""

    def __init__(
        self,
        host: _SubprocessSessionHost,
        config: Any | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._host = host
        self._options = dict(options or {})
        if config is not None:
            self._host._config = config

    def transform(self, record: ExtractedRecord) -> ExtractedRecord | dict[str, Any] | None:
        response = self._host.call(
            "transformer.transform",
            {"record": record_to_dict(record), "options": self._options},
        )
        transformed = response.get("record")
        if transformed is not None:
            if not isinstance(transformed, dict):
                raise TypeError("transformer.transform 的 record 必须是对象")
            return dict_to_record(transformed)
        data = response.get("data")
        if data is not None and not isinstance(data, dict):
            raise TypeError("transformer.transform 的 data 必须是对象")
        return data

    def close(self) -> None:
        self._host.close()


class SubprocessExporterAdapter:
    """契约 2 exporter；记录读取仍必须通过 ``records.read`` 能力代理。"""

    def __init__(self, host: _SubprocessSessionHost) -> None:
        self._host = host

    def __call__(
        self,
        config: Any,
        state: Any,
        run_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._host._config = config
        self._host._state_store = state
        self._host._run_id = run_id
        self._host._broker = None
        return self._host.call(
            "exporter.export",
            {"run_id": run_id, "options": dict(options or {})},
        )

    def close(self) -> None:
        self._host.close()


class SubprocessHookAdapter:
    """将宿主生命周期事件转换为 ``handle('hook.<event>', payload)``。"""

    def __init__(self, host: _SubprocessSessionHost) -> None:
        self._host = host

    def callback(self, event: str) -> Any:
        def dispatch(**context: Any) -> dict[str, Any]:
            run_id = context.get("run_id")
            if run_id is not None and str(run_id) != self._host._run_id:
                self._host._run_id = str(run_id)
                self._host._broker = None
            return self._host.call(
                f"hook.{event}",
                {key: _hook_json_value(value) for key, value in context.items()},
            )

        return dispatch

    def close(self) -> None:
        self._host.close()

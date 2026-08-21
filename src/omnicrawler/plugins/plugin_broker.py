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

# 能力 → 所需 manifest 权限（None = 内置，无需声明）
_CAPABILITY_PERMISSIONS: dict[str, str | None] = {
    "records.read": "records:read",
    "records.write": "records:write",
    "artifacts.read": "artifacts:read",
    "network.fetch": "network:scoped",
    "temp.open": "temp:write",
    "files.read": "files:read",
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
        secrets_allowlist: tuple[str, ...] = (),
        secret_resolver: Callable[[str], str | None] | None = None,
        audit_hook: Callable[[str, dict[str, Any]], None] | None = None,
        plugin_id: str = "",
        trace_full: bool = False,
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
        # O 密钥零暴露：secrets 白名单 + 宿主解析器（插件进程不可见密钥库）
        self._secrets_allowlist = {str(s) for s in secrets_allowlist}
        self._secret_resolver = secret_resolver
        # C6 审计：audit_hook(action, details)；trace_full=False 时降采样（op_counts）
        self._audit_hook = audit_hook
        self._plugin_id = plugin_id
        self._trace_full = trace_full
        self.trace_log: list[dict[str, Any]] = []  # 仅 trace_full 时填充
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

    # ---- 各能力宿主实现 ----

    def _cap_system_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._system_info)

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

    def _cap_artifacts_read(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._dataset is None:
            raise CapabilityError(E_INTERNAL, "宿主未提供 DatasetReader")
        infos = self._dataset.artifacts()
        return {"artifacts": [{"name": a.name, "size": a.size_bytes} for a in infos]}

    def _cap_network_fetch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._network is None:
            raise CapabilityError(E_PERMISSION, "会话未授予网络能力（domains 未声明？）")
        url = str(payload.get("url", ""))
        if not url.startswith(("http://", "https://")):
            raise CapabilityError(E_CONTRACT, "network.fetch 仅支持 http(s) URL")
        from ..core.errors import EgressBudgetExceededError, EgressDisabledError

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
        import base64

        return {
            "status": result.status,
            "url": result.final_url,
            "body_b64": base64.b64encode(result.body).decode("ascii"),
        }

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
        """files:read（Phase 2b 正式化）：manifest input_files 白名单。"""
        path = str(payload.get("path", ""))
        allowed = set(self._input_files)
        if path not in allowed:
            raise CapabilityError(E_PERMISSION, f"路径不在 input_files 白名单: {path}")
        candidate = Path(path).resolve()
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise CapabilityError(E_RESOURCE, f"读取失败: {exc}") from exc
        import base64

        return {"content_b64": base64.b64encode(data).decode("ascii"), "size": len(data)}

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

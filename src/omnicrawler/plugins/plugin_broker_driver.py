"""宿主侧 IPC 循环驱动：能力代理请求与 handle 响应的混排分流。

从 plugin_broker.py 迁出（P1-3 第二批）。为**避免与 plugin_broker 形成静态导入环**
（架构门禁 tools/check_architecture.py 会遍历 TYPE_CHECKING 内的导入），
本模块不导入 ``CapabilityBroker``，而是用下面的 ``_BrokerLike`` 结构协议描述
所需的最小契约（仅 ``dispatch``）；运行期只做属性调用，行为不变。
外部引用方（plugin_contract_suite / plugin_subprocess_adapter / 测试）继续从
plugin_broker 导入 drive_loop（已再导出）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from .plugin_broker_contracts import E_CONTRACT, E_INTERNAL, E_RESOURCE, CapabilityError

LOGGER = logging.getLogger(__name__)


class _BrokerLike(Protocol):
    """CapabilityBroker 的最小结构契约（避免与 plugin_broker 形成静态导入环）。"""

    def dispatch(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def drive_loop(
    session: Any,
    broker: _BrokerLike,
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

def _answer_capability(proc: Any, broker: _BrokerLike, message: dict[str, Any]) -> None:
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

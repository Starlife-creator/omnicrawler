"""契约 2 子进程宿主（Phase 2a C1/C4）。

以 ``python -I -S``（源码模式）或 ``omnicrawler-sandbox-host.exe``（生产模式，
PyInstaller onefile，不含 omnicrawler 与任何宿主依赖）启动，隔离由 bundle
构成 + OS 沙箱保证（方案 C1：生产路径唯一）。

协议（JSON-RPC over stdin/stdout v1，方案 C4）：
    请求: {"v": 1, "operation": "...", "payload": {...}, "request_id": "..."}
    响应: {"request_id": "...", "ok": true,  "result": {...}}
    错误: {"request_id": "...", "ok": false, "error": {"code": "...", "message": "..."}}

生命周期：session 模式（方案 C1a）——每行一个请求，顺序调用复用同一进程；
``{"operation": "session.end"}`` 收尾退出。单次调用方（无 request_id 的旧
形态）同样支持，执行一次即退出，保持 IsolatedPluginRunner 兼容。

本模块只能依赖标准库——它会被冻结进最小宿主 exe（不含 omnicrawler）。
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback

PROTOCOL_VERSION = 1
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024  # stdout 单条上限 8MB（C4/N2 硬化同源）

E_CONTRACT = "E_CONTRACT"
E_INTERNAL = "E_INTERNAL"


def _error_response(request_id: object, code: str, message: str) -> dict:
    return {"request_id": request_id, "ok": False, "error": {"code": code, "message": message}}


def _write_response(response: dict) -> bool:
    """写一条响应；超出输出上限时替换为 E_RESOURCE 语义的截断错误。"""
    line = json.dumps(response, ensure_ascii=False)
    if len(line.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        line = json.dumps(
            _error_response(
                response.get("request_id"),
                "E_RESOURCE",
                f"输出超过上限（{_MAX_OUTPUT_BYTES} 字节），已截断",
            ),
            ensure_ascii=False,
        )
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return True


def _dispatch(handler, operation: str, payload: dict, request_id: object) -> dict:
    """执行插件 handle 并把返回值/异常收敛为 C4 响应形态。"""
    try:
        result = handler(operation, payload)
    except Exception as exc:  # noqa: BLE001 - 插件异常必须收敛为协议错误，不能炸宿主
        return _error_response(request_id, E_INTERNAL, f"{type(exc).__name__}: {exc}")
    if not isinstance(result, dict):
        return _error_response(request_id, E_CONTRACT, "handle 返回值必须是 dict")
    return {"request_id": request_id, "ok": True, "result": result}


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    # 父进程用 -I 启动时 PYTHONIOENCODING 不生效；stdout 恒 UTF-8 在此坐实。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    entry_module, plugin_root = sys.argv[1], sys.argv[2]
    # 入口模块名受控校验（防路径注入，方案 C1：参数均为受控标识符）
    if not entry_module.isidentifier():
        return 2

    sys.path.insert(0, plugin_root)
    try:
        module = importlib.import_module(entry_module)
    except Exception as exc:  # noqa: BLE001
        _write_response(_error_response(None, E_INTERNAL, f"入口模块加载失败: {exc}"))
        return 1
    handler = getattr(module, "handle", None)
    if not callable(handler):
        _write_response(_error_response(None, E_CONTRACT, "插件必须导出 handle(operation, payload)"))
        return 1

    # session 循环：每行一个 JSON 请求；EOF（stdin 关闭）即退出
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0  # stdin 关闭：宿主终止（崩溃清理路径），安静退出
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("请求必须是 JSON 对象")
        except (json.JSONDecodeError, ValueError) as exc:
            _write_response(_error_response(None, E_CONTRACT, f"请求解析失败: {exc}"))
            continue

        operation = str(request.get("operation", ""))
        request_id = request.get("request_id")
        if operation == "session.end":
            _write_response({"request_id": request_id, "ok": True, "result": {}})
            return 0
        payload = request.get("payload")
        if not isinstance(payload, dict):
            _write_response(_error_response(request_id, E_CONTRACT, "payload 必须是 dict"))
            continue
        _write_response(_dispatch(handler, operation, payload, request_id))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # noqa: BLE001 - 最后防线：宿主绝不向 stdout 泄漏 traceback 之外的东西
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)

"""Manifest validation and fail-closed subprocess boundary for local plugins.

Phase 2a（C1/C1a/C4）：
- 启动命令经 ``plugin_backend.resolve_backend_command()`` 解析——冻结产物用
  最小宿主 exe（omnicrawler-sandbox-host，不含 omnicrawler），源码模式用
  ``python -I -S``。
- ``PluginSubprocessSession``：每 run 每插件一次 spawn，会话内多次
  handle 调用复用同一进程（顺序调用，单请求-单响应协议不变）。
- ``IsolatedPluginRunner.call`` 保留为一次性兼容封装（spawn→call→end）。

协议（JSON-RPC over stdin/stdout v1）：请求/响应各一行 JSON；错误码权威
清单见 plugin_subprocess.py（E_CONTRACT/E_PERMISSION/E_QUOTA/E_RESOURCE/
E_INTERNAL/E_EGRESS_BLOCKED/E_UNSUPPORTED_ENV）。
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import plugin_backend

ALLOWED_PERMISSIONS = {"records:read", "records:write", "artifacts:read", "network:scoped", "temp:write"}


@dataclass(frozen=True, slots=True)
class PluginPackageManifest:
    plugin_id: str
    version: str
    publisher: str
    compatible_core: str
    permissions: tuple[str, ...]
    signature: str

    def validate(self, approved_permissions: set[str]) -> None:
        if not self.plugin_id or not self.version or not self.publisher or not self.signature:
            raise ValueError("插件必须包含ID、版本、发布者和签名")
        unknown = set(self.permissions) - ALLOWED_PERMISSIONS
        if unknown:
            raise ValueError(f"插件声明未知权限: {sorted(unknown)}")
        missing = set(self.permissions) - approved_permissions
        if missing:
            raise PermissionError(f"插件权限尚未批准: {sorted(missing)}")


def _subprocess_env() -> dict[str, str]:
    """子进程 env 白名单（B01-016：PYTHON* 均不传）。

    Windows 解释器初始化依赖 SystemRoot/TEMP——全量替换会丢，历史引发
    _Py_HashRandomization_Init 偶发失败，故继承保留这四个键。
    """
    env = {"OMNICRAWL_PLUGIN_SANDBOX": "1"}
    for key in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


class PluginSubprocessSession:
    """会话模式运行器：一次 spawn、多次顺序调用（C1a）。

    异常路径：任一调用超时/崩溃 → 会话终止（kill），后续调用直接抛
    RuntimeError（E_RESOURCE 语义，不自动重 spawn，保持确定性）。
    """

    def __init__(
        self,
        plugin_root: Path,
        entry_module: str,
        *,
        timeout_seconds: float = 30.0,
        handshake_timeout: float | None = None,
    ) -> None:
        root = plugin_root.resolve()
        if not root.is_dir():
            raise ValueError("插件路径无效")
        if not entry_module.isidentifier():
            raise ValueError("插件入口模块名必须是合法标识符")
        self.plugin_root = root
        self.entry_module = entry_module
        self.timeout_seconds = max(0.1, timeout_seconds)
        self._counter = itertools.count(1)
        self._proc: subprocess.Popen[str] | None = None
        self._handshake_timeout = handshake_timeout
        self._first_call = True

    # ---- 生命周期 ----

    def start(self) -> None:
        command, default_handshake = plugin_backend.resolve_backend_command()
        command = [*command, self.entry_module, str(self.plugin_root)]
        kwargs: dict[str, Any] = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=self.plugin_root,
            env=_subprocess_env(),
        )
        if sys.platform == "win32":
            # CREATE_NO_WINDOW（0x08000000，env_checker.py:62 模式）
            kwargs["creationflags"] = 0x08000000
        try:
            self._proc = subprocess.Popen(command, **kwargs)
        except FileNotFoundError as exc:
            raise RuntimeError(f"沙箱后端不可用: {exc}") from exc
        self._handshake_timeout = self._handshake_timeout or default_handshake

    def end(self) -> None:
        """发送 session.end 并回收进程；已终止时静默。"""
        if self._proc is None or self._proc.poll() is not None:
            self._proc = None
            return
        try:
            self._request("session.end", {}, expect_response=True)
        except (RuntimeError, ValueError, OSError):
            pass
        self._kill()

    def _kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._proc = None

    def __enter__(self) -> PluginSubprocessSession:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.end()

    # ---- 调用 ----

    def call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """会话内顺序调用；返回响应 result（ok=false 抛 RuntimeError 带错误码）。"""
        return self._request(operation, payload)

    def _request(self, operation: str, payload: dict[str, Any], expect_response: bool = True) -> dict[str, Any]:
        if self._proc is None:
            self.start()
        proc = self._proc
        assert proc is not None and proc.stdin is not None and proc.stdout is not None
        if proc.poll() is not None:
            raise RuntimeError("E_RESOURCE: 插件会话已终止")
        request_id = f"r{next(self._counter)}"
        request = {"v": 1, "operation": operation, "payload": payload, "request_id": request_id}
        # 首次调用放宽握手时限（冻结冷启动 onefile 自解压 60s，源码模式 30s）；
        # 后续调用恢复普通超时。
        effective_timeout = self.timeout_seconds
        if self._first_call and self._handshake_timeout:
            effective_timeout = self._handshake_timeout
            self._first_call = False
        try:
            proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            if not expect_response:
                return {}
            response = self._read_response(proc, effective_timeout)
        except (OSError, ValueError) as exc:
            self._kill()
            raise RuntimeError(f"E_RESOURCE: 插件进程通信失败: {exc}") from exc
        if not response.get("ok", False):
            error = response.get("error", {})
            raise RuntimeError(f"{error.get('code', 'E_INTERNAL')}: {error.get('message', '插件执行失败')}")
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError("E_CONTRACT: 插件返回值必须是对象")
        return result

    def _read_response(self, proc: subprocess.Popen[str], timeout: float) -> dict[str, Any]:
        """带超时读一行响应（超时杀进程，E_RESOURCE 语义）。

        用后台线程 + join(timeout)：selectors 在 Windows 不支持管道 fd，
        线程方案跨平台一致。
        """
        import threading

        holder: dict[str, Any] = {}

        def _read() -> None:
            try:
                holder["line"] = proc.stdout.readline()  # type: ignore[union-attr]
            except OSError as exc:
                holder["error"] = exc

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            self._kill()
            raise TimeoutError("响应超时")
        if "error" in holder:
            raise holder["error"]
        line = holder.get("line", "")
        if not line:
            stderr_tail = ""
            if proc.stderr is not None:
                try:
                    stderr_tail = proc.stderr.read()[-500:]
                except OSError:
                    pass
            raise RuntimeError(f"插件进程意外退出{f': {stderr_tail}' if stderr_tail else ''}")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("响应必须是 JSON 对象")
        return value


class IsolatedPluginRunner:
    """一次性兼容封装：spawn → call → end（既有调用方不变）。"""

    def __init__(self, plugin_root: Path, *, timeout_seconds: float = 30.0) -> None:
        self.plugin_root = plugin_root.resolve()
        self.timeout_seconds = max(0.1, timeout_seconds)

    def call(self, entry_module: str, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = PluginSubprocessSession(self.plugin_root, entry_module, timeout_seconds=self.timeout_seconds)
        with session:
            return session.call(operation, payload)

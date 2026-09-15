from __future__ import annotations

import contextlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from multiprocessing.connection import Client
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..core.config import load_config
from ..core.utils import atomic_write, utcnow
from ..services.application_service import ApplicationService


@dataclass(frozen=True, slots=True)
class WorkerSession:
    session_id: str
    config_path: str
    workspace: str
    address: str
    family: str
    auth_token: str
    pid: int
    status: str
    created_at: str


@runtime_checkable
class ExecutionBackend(Protocol):
    def start(self, config_path: str | Path) -> dict[str, Any]: ...
    def attach(self, session_file: str | Path) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...
    def pause(self) -> dict[str, Any]: ...
    def resume(self) -> dict[str, Any]: ...
    def stop(self) -> dict[str, Any]: ...


class InProcessBackend:
    """Development/test backend. Desktop production should use LocalWorkerBackend."""

    def __init__(self) -> None:
        self._service: ApplicationService | None = None
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"status": "idle"}
        self._lock = threading.Lock()

    def start(self, config_path: str | Path) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            raise RuntimeError("已有进程内任务正在运行")
        service = ApplicationService(config_path)
        self._service = service
        self._state = {"status": "running", "config_path": str(Path(config_path).resolve())}

        def run() -> None:
            try:
                result = service.run()
            except Exception as exc:
                with self._lock:
                    self._state = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            else:
                # S2.5.47：非 dict 返回也正常置终态，不卡 running
                if isinstance(result, dict):
                    with self._lock:
                        self._state = {"status": result.get("status", "succeeded"), "result": result}
                else:
                    with self._lock:
                        self._state = {
                            "status": "succeeded",
                            "result": {"status": "succeeded", "value": result},
                        }

        self._thread = threading.Thread(target=run, name="omnicrawler-in-process", daemon=True)
        self._thread.start()
        return self.status()

    def attach(self, _session_file: str | Path) -> dict[str, Any]:
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def pause(self) -> dict[str, Any]:
        return self._control("pause")

    def resume(self) -> dict[str, Any]:
        return self._control("resume")

    def stop(self) -> dict[str, Any]:
        return self._control("stop")

    def _control(self, action: str) -> dict[str, Any]:
        if self._service is None:
            raise RuntimeError("没有活动任务")
        return getattr(self._service, action)()


#: POSIX `sun_path` 上限：Linux 108 / macOS 104 字节（**含结尾 NUL**）——留余量取 96。
UNIX_SOCKET_PATH_BUDGET = 96

#: 套接字所在子目录名（放在系统临时区根下）。
_SOCKET_DIR_NAME = "omnicrawler"


def _socket_roots() -> tuple[Path, ...]:
    """套接字根目录候选：系统临时区**越短越优先**（`/tmp` 通常最短，macOS 的 gettempdir 稍长）。"""
    roots: list[Path] = []
    for candidate in (Path("/tmp"), Path(tempfile.gettempdir())):
        with contextlib.suppress(OSError):
            if candidate.is_dir() and candidate not in roots:
                roots.append(candidate)
    return tuple(roots)


def _worker_socket_path(
    workspace: Path, session_id: str, *, roots: Sequence[Path] | None = None
) -> Path:
    """worker 的 AF_UNIX 套接字路径：**短、确定、每会话唯一**。

    **为什么不能放在工作区里**（S2.5 修正）：POSIX `sun_path` 上限是 Linux 108 / macOS 104 字节，
    而工作区可以很深——实测 CI 的 pytest 临时目录、以及用户把项目放在深层目录时都会超限，
    于是本地 worker **根本起不来**：`RuntimeError: 本地Worker启动超时: AF_UNIX path too long`。
    这在 macOS 与 ubuntu 上都能复现，属**产品缺陷**而不是测试环境问题。

    候选按「路径更短优先」取第一个**满足预算**的；都不满足才退回工作区
    （保持旧行为，让失败信息仍然可诊断）。
    """
    name = f"{session_id[:16]}.sock"
    for root in roots if roots is not None else _socket_roots():
        candidate = root / _SOCKET_DIR_NAME / name
        if len(str(candidate).encode("utf-8")) <= UNIX_SOCKET_PATH_BUDGET:
            return candidate
    return workspace / f".worker-{name}"


def _prepare_socket_dir(path: Path) -> None:
    """建套接字目录并收紧权限（POSIX 0700）：只有本用户能连。

    `multiprocessing.connection` 已有 `authkey` 认证，收窄文件权限是**第二道**——
    否则同机其它用户可以尝试对 authkey 做暴力验证。工作区路径下没有这个问题，
    换到系统临时区后必须显式收紧。
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(parent, 0o700)


def _worker_address(
    workspace: Path, session_id: str, *, is_windows: bool | None = None
) -> tuple[str, str]:
    """返回 ``(family, address)``。

    Windows 走命名管道（`\\\\.\\pipe\\...`，无路径长度限制）；
    POSIX 走**系统临时区里的短路径** AF_UNIX（见 :func:`_worker_socket_path`）。
    """
    windows = (os.name == "nt") if is_windows is None else is_windows
    if windows:
        return "AF_PIPE", rf"\\.\pipe\omnicrawler-{session_id}"
    socket_path = _worker_socket_path(workspace, session_id)
    _prepare_socket_dir(socket_path)
    return "AF_UNIX", str(socket_path)


class LocalWorkerBackend:
    """Authenticated detached local-worker backend with reconnectable session metadata."""

    def __init__(self, worker_command: list[str] | None = None) -> None:
        # F35：允许调用方显式指定 worker 命令（含用户手动选择的配套可执行文件）
        self._worker_command = worker_command
        self.session: WorkerSession | None = None
        self.session_file: Path | None = None
        # 持有 worker 的 Popen 句柄：**必须在关闭时回收**，否则 POSIX 上退出的 worker
        # 会变成僵尸进程——`psutil.pid_exists()`（`os.kill(pid, 0)`）对僵尸**仍返回真**，
        # 于是"任务结束后 worker 未退出"会被误判为资源残留（实测 macOS CI；Windows 无此概念）。
        self._process: subprocess.Popen[bytes] | None = None

    def start(self, config_path: str | Path) -> dict[str, Any]:
        config = load_config(config_path)
        config.workspace.mkdir(parents=True, exist_ok=True)
        session_id = uuid.uuid4().hex
        # S2.5 修正：套接字不再放工作区里（POSIX sun_path 上限会被深工作区撑爆 ⇒ worker 起不来）
        family, address = _worker_address(config.workspace, session_id)
        self.session_file = config.workspace / "worker-session.json"
        session = WorkerSession(
            session_id, str(config.path), str(config.workspace), address, family,
            secrets.token_urlsafe(32), 0, "starting", utcnow(),
        )
        _write_session(self.session_file, session)
        log_path = config.workspace / "logs" / "local-worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        creationflags = 0
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        else:
            kwargs["start_new_session"] = True
        with log_path.open("ab") as log:
            if self._worker_command is not None:
                # F35：显式 worker 命令（用户手动选择的配套可执行文件）
                command = [*self._worker_command, "--session", str(self.session_file)]
            else:
                worker_executable = Path(sys.executable).resolve().parent / "omnicrawler-worker.exe"
                command = (
                    [str(worker_executable), "--session", str(self.session_file)]
                    if getattr(sys, "frozen", False) and worker_executable.is_file()
                    else [sys.executable, "-m", "omnicrawler.runtime.worker_main", "--session", str(self.session_file)]
                )
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                creationflags=creationflags, **kwargs,
            )
        self._process = process
        self.session = WorkerSession(**{**asdict(session), "pid": process.pid})
        _write_session(self.session_file, self.session)
        # F36：冻结模式冷启动（解压/杀软首扫）握手放宽到 60s
        deadline = time.monotonic() + (60 if getattr(sys, "frozen", False) else 10)
        last_error = ""
        while time.monotonic() < deadline:
            try:
                return self.status()
            except (OSError, EOFError, ConnectionError) as exc:
                last_error = str(exc)
                time.sleep(0.05)
        # S2.5.21：超时错误信息兜底非空，不再输出 "本地Worker启动超时: " 尾随空白
        raise RuntimeError(f"本地Worker启动超时: {last_error or 'Worker未在时限内就绪（可查看工作区 logs/local-worker.log）'}")

    def attach(self, session_file: str | Path) -> dict[str, Any]:
        self.session_file = Path(session_file).expanduser().resolve()
        self.session = _read_session(self.session_file)
        return self.status()

    def status(self) -> dict[str, Any]:
        return self._request("status")

    def pause(self) -> dict[str, Any]:
        return self._request("pause")

    def resume(self) -> dict[str, Any]:
        return self._request("resume")

    def stop(self) -> dict[str, Any]:
        return self._request("stop")

    def shutdown(self) -> dict[str, Any]:
        response = self._request("shutdown")
        self.reap(timeout=10.0)
        return response

    def reap(self, *, timeout: float = 10.0) -> bool:
        """回收（wait）本进程启动的 worker 子进程；返回是否已回收。

        **为什么必须在关闭时回收**：worker 退出后若没人 `wait()`，在 POSIX 上会留下**僵尸**，
        而 `psutil.pid_exists()` / `os.kill(pid, 0)` 对僵尸**仍返回真** ⇒ 看起来像资源残留
        （实测 macOS CI 的 6 条端到端用例）。Windows 无僵尸概念，所以本地不复现。
        `attach()` 重连进来的后端不是父进程，无法回收（此时这里安全地什么都不做）。
        """
        process = self._process
        if process is None:
            return True
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=timeout)
        self._process = None
        return process.returncode is not None

    def _request(self, command: str) -> dict[str, Any]:
        if self.session is None:
            if self.session_file is None:
                raise RuntimeError("尚未启动或连接Worker")
            self.session = _read_session(self.session_file)
        connection = Client(
            self.session.address,
            family=self.session.family,
            authkey=self.session.auth_token.encode("utf-8"),
        )
        try:
            connection.send({"command": command})
            response = connection.recv()
        finally:
            connection.close()
        if not isinstance(response, dict):
            raise RuntimeError("Worker返回了无效响应")
        if not response.get("ok", False):
            raise RuntimeError(str(response.get("error", "Worker操作失败")))
        return dict(response.get("result", {}))


class FutureRemoteBackend:
    """Interface reservation only; intentionally not exposed in desktop UI."""

    def _unavailable(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("远程执行后端尚未发布")

    start = attach = status = pause = resume = stop = _unavailable


def _write_session(path: Path, session: WorkerSession) -> None:
    # S2.5.21：IPC 安全核心是随机的 auth_token（连接须 authkey 匹配），
    # 不依赖 chmod 0600（Windows 无 POSIX 权限语义，chmod 仅尽力而为）。
    atomic_write(path, json.dumps(asdict(session), ensure_ascii=False, indent=2).encode("utf-8"))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_session(path: Path) -> WorkerSession:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Worker会话文件无效")
    return WorkerSession(**value)

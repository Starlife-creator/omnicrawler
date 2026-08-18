from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
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


class LocalWorkerBackend:
    """Authenticated detached local-worker backend with reconnectable session metadata."""

    def __init__(self, worker_command: list[str] | None = None) -> None:
        # F35：允许调用方显式指定 worker 命令（含用户手动选择的配套可执行文件）
        self._worker_command = worker_command
        self.session: WorkerSession | None = None
        self.session_file: Path | None = None

    def start(self, config_path: str | Path) -> dict[str, Any]:
        config = load_config(config_path)
        config.workspace.mkdir(parents=True, exist_ok=True)
        session_id = uuid.uuid4().hex
        family = "AF_PIPE" if os.name == "nt" else "AF_UNIX"
        address = (
            rf"\\.\pipe\omnicrawler-{session_id}"
            if family == "AF_PIPE"
            else str(config.workspace / f".worker-{session_id}.sock")
        )
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
        return self._request("shutdown")

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
